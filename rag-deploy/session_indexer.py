#!/usr/bin/env python3
"""
Session Indexer — индексирует сессии из SQLite state.db в ChromaDB.

Читает из: /root/.autolycus/state.db (таблицы sessions + messages)
Пишет в:   ChromaDB collection 'sessions'

Каждая сессия разбивается на чанки по N сообщений с перекрытием.

Использование:
    python3 session_indexer.py              # полная индексация
    python3 session_indexer.py --incremental # только новые/изменённые
    python3 session_indexer.py --stats       # статистика
    python3 session_indexer.py --clear       # очистить session collection
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests
import chromadb
from chromadb.config import Settings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rag_config import EMBEDDING_URL, EMBEDDING_MODEL, CHROMA_PATH, EMBEDDING_DIM, SESSION_COLLECTION_NAME, INDEX_SESSIONS_IN_WIKI, SESSION_EMBED_PREFIX, COLLECTION_NAME

STATE_DB = "/root/.autolycus/state.db"
SESSION_COLLECTION = "sessions"

# Параметры чанкинга
CHUNK_SIZE = 10       # сообщений в чанке
CHUNK_OVERLAP = 2     # перекрытие между чанками


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embedding через API."""
    prefixed = [
        f"Instruct: Given a search query, retrieve relevant conversation passages\nQuery: {t[:2500]}"
        for t in texts
    ]
    try:
        resp = requests.post(EMBEDDING_URL, json={
            "model": EMBEDDING_MODEL,
            "input": prefixed
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        data["data"].sort(key=lambda x: x["index"])
        return [item["embedding"] for item in data["data"]]
    except Exception as e:
        print(f"  ⚠ Embedding error: {e}", file=sys.stderr)
        raise


def get_sessions(conn: sqlite3.Connection, incremental: bool = False, state: dict = None) -> list[dict]:
    """Получает сессии из БД."""
    cur = conn.cursor()
    cur.execute("""
        SELECT s.id, s.source, s.model, s.title, s.started_at, s.message_count,
               s.user_id, s.ended_at, s.end_reason
        FROM sessions s
        ORDER BY s.started_at DESC
    """)
    sessions = []
    for row in cur.fetchall():
        sid, source, model, title, started_at, msg_count, user_id, ended_at, end_reason = row
        # Фильтруем пустые сессии
        if not msg_count or msg_count < 2:
            continue
        # Incremental: пропускаем если message_count не изменился
        if incremental and state:
            key = f"{sid}"
            if state.get(key) == msg_count:
                continue
        sessions.append({
            "id": sid,
            "source": source or "unknown",
            "model": model or "unknown",
            "title": title or "",
            "started_at": started_at,
            "message_count": msg_count,
            "user_id": user_id or "",
            "ended_at": ended_at,
            "end_reason": end_reason or "",
        })
    return sessions


def get_messages(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Получает сообщения сессии."""
    cur = conn.cursor()
    cur.execute("""
        SELECT role, content, timestamp, tool_name, token_count
        FROM messages
        WHERE session_id = ?
        ORDER BY timestamp ASC
    """, (session_id,))
    messages = []
    for row in cur.fetchall():
        role, content, ts, tool_name, tokens = row
        if not content:
            continue
        # Форматируем
        if role == "user":
            # Убираем префикс [Николай Гусев] если есть
            text = content
            if "]" in text[:50]:
                idx = text.find("]")
                if idx > 0 and idx < 50:
                    text = text[idx+1:].strip()
            messages.append({"role": "user", "content": text, "ts": ts})
        elif role == "assistant":
            messages.append({"role": "assistant", "content": content, "ts": ts})
        elif role == "tool":
            # Инструментальные вызовы пропускаем (слишком шумные)
            pass
    return messages


def session_to_chunks(session: dict, messages: list[dict]) -> list[dict]:
    """Разбивает сессию на чанки по CHUNK_SIZE сообщений."""
    if not messages:
        return []

    chunks = []
    for i in range(0, len(messages), CHUNK_SIZE - CHUNK_OVERLAP):
        chunk_msgs = messages[i:i + CHUNK_SIZE]
        if not chunk_msgs:
            continue

        lines = []
        for m in chunk_msgs:
            role = m["role"]
            content = m["content"].strip()
            if content:
                lines.append(f"[{role}] {content}")

        text = "\n\n".join(lines)
        if len(text) < 50:
            continue

        start_msg = chunk_msgs[0]["content"][:100]
        end_msg = chunk_msgs[-1]["content"][:100]

        # Дата из started_at
        started_at = session.get("started_at", 0)
        date_str = time.strftime("%Y-%m-%d", time.localtime(started_at)) if started_at else "unknown"

        chunks.append({
            "text": text,
            "metadata": {
                "source": f"sessions/{session['id']}",
                "session_id": session["id"],
                "platform": session["source"],
                "model": session["model"],
                "date": date_str,
                "started_at": started_at,
                "msg_start": i,
                "msg_end": i + len(chunk_msgs),
                "msg_count": len(chunk_msgs),
                "heading": f"Messages {i+1}-{i+len(chunk_msgs)}",
                "start_preview": start_msg,
                "end_preview": end_msg,
                "char_count": len(text),
                "session_title": session.get("title", ""),
                "end_reason": session.get("end_reason", ""),
            }
        })

        if i + CHUNK_SIZE >= len(messages):
            break

    return chunks


def index_all(incremental: bool = False):
    print(f"📚 Session Indexer", flush=True)
    print(f"   State DB: {STATE_DB}", flush=True)
    print(f"   Embedding: {EMBEDDING_MODEL} ({EMBEDDING_DIM}d)", flush=True)
    print(f"   Chroma: {CHROMA_PATH}/{SESSION_COLLECTION}", flush=True)
    print(f"   Index sessions in wiki: {INDEX_SESSIONS_IN_WIKI}", flush=True)

    conn = sqlite3.connect(STATE_DB)

    # Загружаем state для incremental
    state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.session_state.json')
    state = {}
    if incremental and os.path.exists(state_path):
        try:
            with open(state_path) as f:
                state = json.load(f)
        except Exception:
            state = {}

    sessions = get_sessions(conn, incremental=incremental, state=state)
    print(f"   Sessions to index: {len(sessions)}", flush=True)

    client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False),
    )

    # ── Sessions collection ──────────────────────────────────────────────
    if not incremental:
        try:
            client.delete_collection(SESSION_COLLECTION)
        except Exception:
            pass
        state = {}

    try:
        sess_collection = client.get_collection(SESSION_COLLECTION)
        print(f"   Existing sessions: {sess_collection.count()} chunks", flush=True)
    except Exception:
        sess_collection = client.create_collection(
            name=SESSION_COLLECTION,
            metadata={"hnsw:space": "cosine"}
        )
        state = {}

    # ── Wiki collection (для session chunks) ────────────────────────────
    wiki_collection = None
    if INDEX_SESSIONS_IN_WIKI:
        try:
            wiki_collection = client.get_collection(COLLECTION_NAME)
            print(f"   Existing wiki: {wiki_collection.count()} chunks", flush=True)
        except Exception:
            wiki_collection = None
            print(f"   ⚠ Wiki collection '{COLLECTION_NAME}' not found, skipping wiki indexing", flush=True)

    batch_texts, batch_ids, batch_metadatas = [], [], []
    # Буферы для wiki (используют другой префикс)
    wiki_texts, wiki_ids, wiki_metadatas = [], [], []
    chunk_idx = 0
    BATCH_SIZE = 16
    indexed_sessions = 0

    def flush_sessions():
        """Flush batch в sessions collection."""
        nonlocal chunk_idx
        if not batch_texts:
            return
        try:
            embs = embed_texts(batch_texts)
            sess_collection.add(embeddings=embs, documents=batch_texts, ids=batch_ids, metadatas=batch_metadatas)
            print(f"   📥 Sessions batch {len(batch_texts)} chunks → Chroma (total: {sess_collection.count()})", flush=True)
        except Exception as e:
            print(f"   ⚠ Sessions batch failed: {e}", flush=True)

    def flush_wiki():
        """Flush batch в wiki collection (session chunks с другим префиксом)."""
        if not wiki_texts or wiki_collection is None:
            return
        try:
            prefixed = [f"{SESSION_EMBED_PREFIX} {t[:2500]}" for t in wiki_texts]
            resp = requests.post(EMBEDDING_URL, json={
                "model": EMBEDDING_MODEL,
                "input": prefixed
            }, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            data["data"].sort(key=lambda x: x["index"])
            embs = [item["embedding"] for item in data["data"]]
            wiki_collection.add(embeddings=embs, documents=wiki_texts, ids=wiki_ids, metadatas=wiki_metadatas)
            print(f"   📥 Wiki batch {len(wiki_texts)} session chunks → Chroma (total: {wiki_collection.count()})", flush=True)
        except Exception as e:
            print(f"   ⚠ Wiki batch failed: {e}", flush=True)

    for session in sessions:
        messages = get_messages(conn, session["id"])
        chunks = session_to_chunks(session, messages)

        if not chunks:
            state[session["id"]] = session["message_count"]
            continue

        for c in chunks:
            batch_texts.append(c["text"])
            batch_ids.append(f"session:{session['id']}#{chunk_idx}")
            batch_metadatas.append(c["metadata"])
            wiki_texts.append(c["text"])
            wiki_ids.append(f"session:{session['id']}#{chunk_idx}")
            wiki_metadatas.append(c["metadata"])
            chunk_idx += 1

            if len(batch_texts) >= BATCH_SIZE:
                flush_wiki()
                flush_sessions()
                batch_texts.clear()
                batch_ids.clear()
                batch_metadatas.clear()
                wiki_texts.clear()
                wiki_ids.clear()
                wiki_metadatas.clear()

        state[session["id"]] = session["message_count"]
        indexed_sessions += 1

    if batch_texts:
        flush_wiki()
        flush_sessions()
        batch_texts.clear()
        batch_ids.clear()
        batch_metadatas.clear()
        wiki_texts.clear()
        wiki_ids.clear()
        wiki_metadatas.clear()

    # Сохраняем state
    with open(state_path + '.tmp', 'w') as f:
        json.dump(state, f)
    os.replace(state_path + '.tmp', state_path)

    conn.close()
    print(f"\n✅ Done! {indexed_sessions} sessions, {sess_collection.count()} session chunks", flush=True)
    if wiki_collection is not None:
        print(f"   Wiki collection: {wiki_collection.count()} total chunks (incl. session chunks)", flush=True)


def show_stats():
    client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False),
    )
    try:
        coll = client.get_collection(SESSION_COLLECTION)
        count = coll.count()
        print(f"📊 Collection '{SESSION_COLLECTION}': {count} chunks")
        if count > 0:
            result = coll.peek(limit=5)
            platforms = set(m.get("platform", "?") for m in result["metadatas"])
            dates = set(m.get("date", "?") for m in result["metadatas"])
            print(f"   Platforms: {platforms}")
            print(f"   Dates: {sorted(dates)[:5]}")
    except Exception as e:
        print(f"⚠ Collection not found: {e}")


def clear_collection():
    client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False),
    )
    try:
        client.delete_collection(SESSION_COLLECTION)
        print(f"🗑 Collection '{SESSION_COLLECTION}' deleted")
    except Exception as e:
        print(f"⚠ {e}")
    state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.session_state.json')
    if os.path.exists(state_path):
        os.remove(state_path)
    print("📝 State reset")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Session Indexer для ChromaDB")
    parser.add_argument("--incremental", action="store_true", help="Только изменённые сессии")
    parser.add_argument("--stats", action="store_true", help="Статистика")
    parser.add_argument("--clear", action="store_true", help="Очистить")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.clear:
        clear_collection()
    else:
        index_all(incremental=args.incremental)

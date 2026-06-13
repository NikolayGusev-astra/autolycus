#!/usr/bin/env python3
"""
ChromaDB Indexer — индексирует wiki/*.md в векторную БД.
Использует text-embedding-multilingual-e5-large-instruct через API.

CRUD-стабильная индексация:
- Детерминистичные ID (хеш от source + heading) — не зависят от порядка файлов
- Delete-before-upsert для incremental — старые чанки заменяются корректно
- Очистка чанков удалённых файлов
- Content-based dedup — пропуск точных дубликатов

Usage:
    python3 rag_indexer.py              # полная индексация
    python3 rag_indexer.py --incremental # только новые/изменённые + cleanup
    python3 rag_indexer.py --stats       # статистика
    python3 rag_indexer.py --clear       # очистить
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml
import chromadb
from chromadb.config import Settings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rag_config import *


# ═══════════════════════════════════════════════════════════════════════════
# Хелперы
# ═══════════════════════════════════════════════════════════════════════════

def chunk_id(source: str, heading: str, seq: int = 0) -> str:
    """Детерминистичный ID чанка: хеш от source + heading + seq.
    
    При повторной индексации того же файла ID не меняется,
    не зависит от порядка файлов или глобального счётчика.
    """
    raw = f"{source}::{heading}::{seq}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def content_hash(text: str) -> str:
    """Хеш содержимого чанка для content-based dedup."""
    return hashlib.md5(text.encode()).hexdigest()[:16]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Извлекает YAML frontmatter и body."""
    text = text.lstrip('\ufeff')
    if not text.startswith('---'):
        return {}, text
    end = text.find('---', 3)
    if end == -1:
        return {}, text
    try:
        meta = yaml.safe_load(text[3:end].strip()) or {}
    except Exception:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), text[end + 3:].strip()


def chunk_markdown(text: str, meta: dict, filepath: str) -> list[dict]:
    """Чанкинг markdown по заголовкам ## и ###.
    
    Каждый чанк получает мета-поле chunk_content_hash для content dedup.
    """
    if not text.strip():
        return []

    lines = text.split('\n')
    chunks = []
    current_heading = "Overview"
    current_lines = []

    def flush():
        nonlocal current_lines
        if not current_lines:
            return
        content = '\n'.join(current_lines).strip()
        if len(content) < 20:
            current_lines = []
            return
        chunks.append({
            "text": content,
            "metadata": {
                "source": filepath,
                "heading": current_heading,
                "char_count": len(content),
            }
        })
        current_lines = []

    for line in lines:
        h_match = re.match(r'^(#{2,4})\s+(.+)$', line)
        if h_match:
            flush()
            current_heading = h_match.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    flush()

    if not chunks and text.strip():
        chunks.append({
            "text": text.strip()[:CHUNK_SIZE],
            "metadata": {
                "source": filepath,
                "heading": "Overview",
                "char_count": min(len(text.strip()), CHUNK_SIZE),
            }
        })

    # Smart split длинных чанков
    final = []
    for chunk in chunks:
        cc = chunk["metadata"]["char_count"]
        if cc <= CHUNK_SIZE:
            final.append(chunk)
        else:
            text = chunk["text"]
            parts = text.split('\n\n')
            if len(parts) > 1:
                buf, blen = [], 0
                for p in parts:
                    to_add = []
                    if len(p) > CHUNK_SIZE:
                        pos = 0
                        while pos < len(p):
                            end = min(pos + CHUNK_SIZE, len(p))
                            if end < len(p):
                                ls = p.rfind(' ', pos, end)
                                if ls > pos:
                                    end = ls
                            to_add.append(p[pos:end].strip())
                            pos = end + 1 if end < len(p) and p[end] == ' ' else end
                    else:
                        to_add = [p]
                    for subp in to_add:
                        if blen + len(subp) > CHUNK_SIZE and buf:
                            final.append({
                                "text": '\n\n'.join(buf),
                                "metadata": {**chunk["metadata"], "char_count": sum(len(x) for x in buf)}
                            })
                            buf, blen = [subp], len(subp)
                        else:
                            buf.append(subp)
                            blen += len(subp)
                if buf:
                    final.append({
                        "text": '\n\n'.join(buf),
                        "metadata": {**chunk["metadata"], "char_count": blen}
                    })
            else:
                pos = 0
                while pos < len(text):
                    end = min(pos + CHUNK_SIZE, len(text))
                    if end < len(text):
                        last_space = text.rfind(' ', pos, end)
                        if last_space > pos:
                            end = last_space
                    final.append({
                        "text": text[pos:end].strip(),
                        "metadata": {**chunk["metadata"], "char_count": end - pos}
                    })
                    pos = end + 1 if end < len(text) and text[end] == ' ' else end
    
    # Добавляем content_hash в каждый чанк
    for c in final:
        c["metadata"]["content_hash"] = content_hash(c["text"])
        c["content_hash"] = c["metadata"]["content_hash"]
    
    return final


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embedding через API с правильным префиксом e5-instruct."""
    prefixed = [
        f"Instruct: Given a wiki search query, retrieve relevant wiki passages\nQuery: {t[:MAX_CHARS_PER_INPUT]}"
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
    except requests.Timeout:
        print(f"  ⚠ Embedding timeout, retrying with smaller batch...", file=sys.stderr)
        results = []
        for t in texts:
            try:
                r = requests.post(EMBEDDING_URL, json={
                    "model": EMBEDDING_MODEL,
                    "input": [f"Instruct: Given a wiki search query, retrieve relevant wiki passages\nQuery: {t[:MAX_CHARS_PER_INPUT]}"]
                }, timeout=120)
                r.raise_for_status()
                results.append(r.json()["data"][0]["embedding"])
            except Exception as e:
                print(f"  ⚠ Single embedding failed: {e}", file=sys.stderr)
                results.append([0.0] * EMBEDDING_DIM)
            time.sleep(0.1)
        return results
    except Exception as e:
        print(f"  ⚠ Embedding error: {e}", file=sys.stderr)
        raise


def file_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# State management
# ═══════════════════════════════════════════════════════════════════════════

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.rag_state.json')


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    with open(STATE_PATH + '.tmp', 'w') as f:
        json.dump(state, f)
    os.replace(STATE_PATH + '.tmp', STATE_PATH)


# ═══════════════════════════════════════════════════════════════════════════
# File collection
# ═══════════════════════════════════════════════════════════════════════════

def collect_md_files(paths: list[str]) -> list[str]:
    import fnmatch
    import re
    files = []
    _auto_finding_re = re.compile(r'^\d{8}-\d{6}-')
    for base in paths:
        if not os.path.isdir(base):
            continue
        for root, dirs, fnames in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not any(
                    fnmatch.fnmatch(d, pat) or fnmatch.fnmatch(os.path.relpath(os.path.join(root, d), base), pat)
                    for pat in EXCLUDE_DIRS
                )
            ]
            for fn in fnames:
                if fn.endswith('.md'):
                    if _auto_finding_re.match(fn):
                        continue
                    files.append(os.path.join(root, fn))
    return sorted(files)


# ═══════════════════════════════════════════════════════════════════════════
# Batch flush
# ═══════════════════════════════════════════════════════════════════════════

def _flush_batch(collection, texts: list, ids: list, metadatas: list, dedup_set: set = None):
    """Flush batch с content-based dedup."""
    if not texts:
        return
    
    # Content-based dedup: пропускаем чанки с уже виденным content_hash
    if dedup_set is not None:
        filtered_texts, filtered_ids, filtered_md = [], [], []
        dedup_count = 0
        for i in range(len(texts)):
            h = metadatas[i].get("content_hash", "")
            if h and h in dedup_set:
                dedup_count += 1
                continue
            filtered_texts.append(texts[i])
            filtered_ids.append(ids[i])
            filtered_md.append(metadatas[i])
        texts, ids, metadatas = filtered_texts, filtered_ids, filtered_md
        
        if dedup_count > 0:
            print(f"   ⏭ Skipped {dedup_count} duplicate chunks (content dedup)", flush=True)
        
        if not texts:
            texts.clear()
            ids.clear()
            metadatas.clear()
            return
        
        # Record new hashes
        for m in metadatas:
            h = m.get("content_hash", "")
            if h:
                dedup_set.add(h)

    try:
        embs = embed_texts(texts)
        collection.upsert(embeddings=embs, documents=texts, ids=ids, metadatas=metadatas)
        print(f"   📥 Batch {len(texts)} chunks → Chroma (total: {collection.count()})", flush=True)
    except Exception as e:
        print(f"   ⚠ Batch failed ({len(texts)} chunks): {e}", flush=True)
        for i in range(len(texts)):
            try:
                emb = embed_texts([texts[i]])
                collection.upsert(embeddings=emb, documents=[texts[i]], ids=[ids[i]], metadatas=[metadatas[i]])
            except Exception as e2:
                print(f"   ⚠ Skip chunk {ids[i]}: {e2}", flush=True)
    texts.clear()
    ids.clear()
    metadatas.clear()


# ═══════════════════════════════════════════════════════════════════════════
# Main index function (CRUD)
# ═══════════════════════════════════════════════════════════════════════════

def index_all(incremental: bool = False):
    print(f"📚 Wiki RAG Indexer", flush=True)
    print(f"   Search paths: {WIKI_PATHS}", flush=True)
    print(f"   Embedding: {EMBEDDING_MODEL} ({EMBEDDING_DIM}d) @ {EMBEDDING_URL}", flush=True)
    print(f"   Chroma: {CHROMA_PATH}/{COLLECTION_NAME}", flush=True)
    print(f"   Mode: {'incremental (CRUD)' if incremental else 'full rebuild'}", flush=True)

    files = collect_md_files(WIKI_PATHS)
    current_rel_set = set(os.path.relpath(fp) for fp in files)
    print(f"   Found {len(files)} .md files", flush=True)

    state = load_state()
    client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False),
    )

    if not incremental:
        # Полная перестройка
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        collection = client.create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
        state = {}
    else:
        try:
            collection = client.get_collection(COLLECTION_NAME)
            print(f"   Existing: {collection.count()} chunks", flush=True)
        except Exception:
            collection = client.create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )
            state = {}

    # ── Шаг 2: Cleanup удалённых файлов ─────────────────────────────────
    if incremental:
        deleted = [rel for rel in state if rel not in current_rel_set]
        if deleted:
            print(f"   🗑 Cleanup {len(deleted)} deleted files:", flush=True)
            for rel in deleted[:10]:
                try:
                    collection.delete(where={"source": rel})
                    print(f"      Deleted chunks for: {rel}", flush=True)
                except Exception as e:
                    print(f"      ⚠ Failed to delete {rel}: {e}", flush=True)
                    pass
            if len(deleted) > 10:
                print(f"      ... and {len(deleted) - 10} more", flush=True)
            # Удаляем из state
            for rel in deleted:
                del state[rel]

    # ── Определяем файлы для индексации ─────────────────────────────────
    todo = []
    for fp in files:
        rel = os.path.relpath(fp)
        fh = file_hash(fp)
        if incremental and state.get(rel) == fh:
            continue
        todo.append((fp, rel, fh))

    if not todo:
        print(f"   ✅ All files up to date ({len(files)} total)", flush=True)
        save_state(state)
        return

    print(f"   Files to index: {len(todo)}", flush=True)

    BATCH_SIZE = 16
    
    # Глобальный set content_hash для dedup в пределах одной сессии индексации
    global_dedup = set()

    for fp, rel, fh in todo:
        # ── Шаг 2 (для incremental): delete-before-upsert ──────────────
        if incremental:
            try:
                # Удаляем старые чанки этого файла
                old_count = 0
                old_meta = collection.get(where={"source": rel})
                if old_meta and old_meta["ids"]:
                    old_count = len(old_meta["ids"])
                    collection.delete(where={"source": rel})
                if old_count > 0:
                    print(f"   🗑 Removed {old_count} old chunks for: {rel}", flush=True)
            except Exception as e:
                print(f"   ⚠ Could not delete old chunks for {rel}: {e}", flush=True)
                pass

        # Читаем и чанкуем
        try:
            with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                raw = f.read()
        except Exception as e:
            print(f"   ⚠ Read error {rel}: {e}", flush=True)
            state[rel] = fh
            continue

        md, body = parse_frontmatter(raw)
        if not body.strip():
            state[rel] = fh
            continue

        chunks = chunk_markdown(body, md, rel)
        if not chunks:
            state[rel] = fh
            continue

        title = md.get("title", "")
        ptype = md.get("type", "")
        tags = ",".join(md.get("tags", [])) if isinstance(md.get("tags"), list) else str(md.get("tags", ""))

        batch_texts, batch_ids, batch_metadatas = [], [], []
        for c in chunks:
            # ── Шаг 3: Content-based dedup ────────────────────────────
            h = c.get("content_hash", "")
            if h and h in global_dedup:
                print(f"   ⏭ Skipped duplicate chunk: {rel} › {c['metadata']['heading']}", flush=True)
                continue
            if h:
                global_dedup.add(h)
            
            # Детерминистичный ID
            cid = chunk_id(rel, c["metadata"]["heading"], 0)
            
            batch_texts.append(c["text"])
            batch_ids.append(cid)
            batch_metadatas.append({
                "source": rel,
                "heading": c["metadata"]["heading"],
                "title": title,
                "type": ptype,
                "tags": tags,
                "char_count": c["metadata"]["char_count"],
                "content_hash": h,
            })

            if len(batch_texts) >= BATCH_SIZE:
                _flush_batch(collection, batch_texts, batch_ids, batch_metadatas)

        if batch_texts:
            _flush_batch(collection, batch_texts, batch_ids, batch_metadatas)

        state[rel] = fh

    save_state(state)
    stats = show_stats(collection=collection)
    print(f"✅ Done! {len(todo)} files, {collection.count()} total chunks", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════════

def show_stats(collection=None):
    client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False),
    )
    try:
        coll = collection or client.get_collection(COLLECTION_NAME)
        count = coll.count()
        print(f"📊 Collection '{COLLECTION_NAME}': {count} chunks", flush=True)
        if count > 0:
            result = coll.peek()
            sources = set(m.get("source", "") for m in result["metadatas"])
            print(f"   Sources: {list(sources)[:5]}", flush=True)
    except Exception as e:
        print(f"⚠ Collection not found: {e}", flush=True)
    state = load_state()
    if state:
        print(f"   Tracked files: {len(state)}", flush=True)
    return count


def clear_collection():
    client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False),
    )
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"🗑 Collection '{COLLECTION_NAME}' deleted")
    except Exception as e:
        print(f"⚠ {e}")
    save_state({})
    print("📝 State reset")


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ChromaDB Indexer для wiki (CRUD)")
    parser.add_argument("--incremental", action="store_true", help="Только изменённые файлы + cleanup удалённых")
    parser.add_argument("--stats", action="store_true", help="Статистика")
    parser.add_argument("--clear", action="store_true", help="Очистить")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.clear:
        clear_collection()
    else:
        index_all(incremental=args.incremental)
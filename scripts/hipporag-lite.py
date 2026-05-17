#!/usr/bin/env python3
"""
HippoRAG Lite v3 — TF-IDF cosine similarity + optional embedding-based semantic search.
- Без LLM, без API-зависимостей
- Токенизация через \w{3,} + стоп-слова (совместимость)
- TF-IDF косинусное сходство для поиска документов (не термов!)
- Опционально: семантический поиск через sentence-transformers (all-MiniLM-L6-v2, ~80MB)
- Результаты: документы (сессии/скиллы), ранжированные по релевантности
- Кэш parsed текста — повторный запуск мгновенный
- Co-occurrence граф сохранён для обратной совместимости

Usage:
  hipporag-lite.py index [--full] [--embed]        — индексация (--embed = добавить эмбеддинги)
  hipporag-lite.py search <query>                   — TF-IDF поиск
  hipporag-lite.py search --embed <query>           — семантический поиск
  hipporag-lite.py search --ppr <query>             — PPR (граф ассоциаций)
  hipporag-lite.py search --hybrid <query>          — гибрид: TF-IDF + Embedding + PPR
  hipporag-lite.py compare <query>                  — сравнить все 3 метода
  hipporag-lite.py seed-test <query>                — отладка токенизации
"""

import os
import sys
import json
import glob
import pickle
import re
import math
import sqlite3
import numpy as np
from scipy.sparse import csr_matrix, load_npz, save_npz
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Dict, Tuple, Set, Optional

# === Config ===
SAVE_DIR = os.path.expanduser("~/.hermes/hipporag")
SKILLS_DIR = os.path.expanduser("~/.hermes/skills")
SESSIONS_DIR = os.path.expanduser("~/.hermes/sessions")
GRAPH_FILE = f"{SAVE_DIR}/knowledge_graph.pkl"
META_FILE = f"{SAVE_DIR}/index_meta.json"
DOCS_FILE = f"{SAVE_DIR}/documents.json"
MANIFEST_FILE = f"{SAVE_DIR}/file_manifest.json"
DOC_CACHE_FILE = f"{SAVE_DIR}/doc_cache.json"
TERMS_FILE = f"{SAVE_DIR}/doc_terms.json"
TFIDF_FILE = f"{SAVE_DIR}/tfidf_vectors.json"
REV_DB_FILE = f"{SAVE_DIR}/rev_index.db"
EMBEDDING_FILE = f"{SAVE_DIR}/embeddings.npy"
EMBEDDING_META_FILE = f"{SAVE_DIR}/embedding_meta.json"
SPARSE_MATRIX_FILE = f"{SAVE_DIR}/ppr_matrix.npz"
NODE_LIST_FILE = f"{SAVE_DIR}/node_list.json"

os.makedirs(SAVE_DIR, exist_ok=True)

# === Stop Words (русские + английские) ===
STOP_WORDS = {
    "это","что","как","для","все","или","еще","уже","так","его",
    "она","они","них","кто","где","там","тут","если","чтобы",
    "когда","тоже","даже","нет","да","вот","потом","себя",
    "него","нее","неё","мне","меня","тебя","тебе","себе","собой",
    "сами","сам","сама","само","нами","вами","ними","нами",
    "потому","поэтому","надо","можно","нужно","будет","было","была",
    "были","быть","есть","очень","просто","вообще","конечно",
    "ладно","хорошо","плохо","нормально","один","два","три","раз",
    "опять","снова","тогда","теперь","сейчас","сегодня","вчера",
    "завтра","всегда","иногда","никогда","весь","вся","всё",
    "этот","эта","эти","тот","та","то","те","такой","такая","такое",
    "такие","мой","моя","мое","мои","твой","твоя","твое","твои",
    "наш","наша","наше","наши","ваш","ваша","ваше","ваши",
    "какой","какая","какое","какие","каждый","любой","другой","другое",
    "более","менее","самый","самая","самое","самые","больше","меньше",
    "всего","всех","всем","том","тем","тех","этом","этим","этих",
    "нему","ним","нем","ней","нею","них","",
    "#","https","http","www","com","ru","org","net","html","nbsp",
    "user","tool","assistant","system","app","apple","notes","saving",
    "file","name","type","set","get","add","new","old","way","line",
    "using","based","need","make","want","looking","trying","going","let",
    "use","used","using","does","done","doing","made","making","help",
    "thing","things","stuff","bit","lot","time","day","week","month",
    "the","and","for","are","but","not","you","all","can","had",
    "her","was","one","our","out","has","have","been","some",
    "them","than","then","they","this","that","with","what","when",
    "where","which","while","will","would","could","should","also",
    "very","just","from","their","there","these","those","about",
    "your","into","over","such","each","only","other","more","most",
    "much","many","now","here","like","well","make","made","said",
    "were","been","being","done","does","doing","used","using",
    "get","got","use","let","see","know","need","want","say","take",
    "come","go","look","find","give","tell","think","work","call",
    "try","ask","put","set","run","keep","show","start","end",
}

# === Tokenization ===
RE_TOKEN = re.compile(r'[а-яёa-z][а-яёa-z0-9_\-]{2,}', re.IGNORECASE)
RE_PURE_NUM = re.compile(r'^\d[\d\.\,\-]*$')

def tokenize(text: str) -> List[str]:
    """Извлечение термов: минимум 3 буквы, не стоп-слово, не число"""
    text = text.lower()
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'\{[^}]*\}', '', text)
    tokens = []
    for m in RE_TOKEN.finditer(text):
        t = m.group().lower().strip('-_')
        if len(t) >= 3 and t not in STOP_WORDS and not RE_PURE_NUM.match(t):
            tokens.append(t)
    return tokens


# === Extract text from session files ===

def parse_session_jsonl(path: str) -> str:
    """Parse JSONL session file → concatenated message text"""
    messages = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if isinstance(msg, dict):
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role == "session_meta" or not isinstance(content, str) or len(content) <= 5:
                        continue
                    messages.append(f"[{role}]: {content[:500]}")
            except json.JSONDecodeError:
                pass
    return "\n".join(messages)


def parse_session_json(path: str) -> str:
    """Parse standard JSON session file → concatenated message text"""
    with open(path) as f:
        data = json.load(f)
    messages = []
    if isinstance(data, dict) and "messages" in data:
        for m in data["messages"]:
            if isinstance(m, dict):
                role = m.get("role", "")
                content = m.get("content", "")
                if isinstance(content, str) and len(content) > 5:
                    messages.append(f"[{role}]: {content[:500]}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                role = item.get("role", "")
                content = item.get("content", "")
                if isinstance(content, str) and len(content) > 5:
                    messages.append(f"[{role}]: {content[:500]}")
    return "\n".join(messages)


# === Document Collection (with text cache) ===

def get_file_type(fname: str) -> str:
    """Определяем тип файла по имени"""
    if fname.startswith("request_dump_"):
        return "api_dump"
    if fname.startswith("session_cron_"):
        return "cron_session"
    if fname.startswith("session_"):
        return "session"
    if re.match(r'^\d{8}_', fname):
        return "old_session"
    return "other"


def collect_documents_with_mtime() -> List[Dict]:
    """
    Collect all documents with mtime.
    Uses doc_cache.json to avoid re-parsing unchanged files.
    """
    docs = []
    doc_cache = {}
    if os.path.exists(DOC_CACHE_FILE):
        try:
            with open(DOC_CACHE_FILE) as f:
                doc_cache = json.load(f)
        except Exception:
            pass

    # Skills
    for skill_file in sorted(glob.glob(f"{SKILLS_DIR}/**/SKILL.md", recursive=True)):
        try:
            with open(skill_file) as f:
                content = f.read()
            rel = os.path.relpath(skill_file, SKILLS_DIR)
            desc_match = re.search(r'description:\s*"([^"]+)"', content)
            desc = desc_match.group(1) if desc_match else ""
            body = re.sub(r'^---.*?---\s*', '', content, count=1, flags=re.DOTALL)
            text = f"Skill: {rel}. {desc}. {body[:1500]}"
            mtime = os.path.getmtime(skill_file)
            docs.append({"text": text, "source": f"skill:{rel}", "type": "skill", "mtime": mtime, "path": skill_file})
        except Exception:
            pass

    # Session files
    all_session_files = []
    if os.path.isdir(SESSIONS_DIR):
        all_session_files.extend((f, ".jsonl") for f in sorted(glob.glob(f"{SESSIONS_DIR}/*.jsonl")))
        all_session_files.extend((f, ".json") for f in sorted(glob.glob(f"{SESSIONS_DIR}/*.json")))

    if all_session_files:
        print(f"   {len(all_session_files)} total session files...", flush=True)

    collected_sources = set()
    for idx, (fp, ext) in enumerate(all_session_files):
        fname = os.path.basename(fp)

        # Skip API dumps — мусор
        if fname.startswith("request_dump_"):
            continue

        # Skip sessions.json и mempalace.yaml и entities.json
        if fname in ("sessions.json", "entities.json", "mempalace.yaml"):
            continue

        if idx > 0 and idx % 1500 == 0:
            print(f"     [{idx}/{len(all_session_files)}] session files...", flush=True)

        try:
            mtime = os.path.getmtime(fp)
            cache_key = fname
            # Проверяем кэш
            text = None
            if cache_key in doc_cache:
                cached_mtime, cached_text = doc_cache[cache_key]
                if cached_mtime == mtime and len(cached_text) >= 100:
                    text = cached_text

            if text is None:
                if ext == ".jsonl":
                    text = parse_session_jsonl(fp)
                else:
                    text = parse_session_json(fp)
                # Кэшируем
                if len(text) >= 100:
                    doc_cache[cache_key] = [mtime, text]

            if len(text) >= 100:
                ftype = get_file_type(fname)
                docs.append({"text": text, "source": f"session:{fname}",
                             "type": ftype, "mtime": mtime, "path": fp})
                collected_sources.add(cache_key)

        except Exception:
            pass

    # Чистим кэш от удалённых файлов
    stale = [k for k in doc_cache if k not in collected_sources and not k.startswith("skill:")]
    for k in stale:
        del doc_cache[k]

    # Сохраняем кэш
    with open(DOC_CACHE_FILE, "w") as f:
        json.dump(doc_cache, f, ensure_ascii=False)

    return docs


# === Manifest ===

def load_manifest() -> dict:
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_manifest(manifest: dict):
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def detect_changed_files(docs: List[Dict], manifest: dict) -> tuple:
    changed = []
    unchanged_sources = set()
    current_sources = set()
    for doc in docs:
        source = doc["source"]
        current_sources.add(source)
        prev_mtime = manifest.get(source)
        if prev_mtime is None or prev_mtime != doc["mtime"]:
            changed.append(doc)
        else:
            unchanged_sources.add(source)
    removed_sources = set(manifest.keys()) - current_sources
    return changed, unchanged_sources, removed_sources


# === Co-occurrence Graph (legacy, built for backward compat) ===

def build_cooc_graph(doc_terms: Dict[str, List[str]], max_terms_per_doc: int = 30) -> 'networkx.Graph':
    """
    Строим взвешенный граф co-occurrence (legacy).
    Ограничиваем max_terms_per_doc — топ-N по частоте в документе.
    """
    import networkx as nx
    G = nx.Graph()
    edge_weights = Counter()
    for source, terms in doc_terms.items():
        freq = Counter(terms)
        unique = sorted(set(terms), key=lambda t: -freq[t])[:max_terms_per_doc]
        for i in range(len(unique)):
            G.add_node(unique[i])
            for j in range(i + 1, len(unique)):
                a, b = unique[i], unique[j]
                if a < b:
                    edge_weights[(a, b)] += 1
                else:
                    edge_weights[(b, a)] += 1
    for (a, b), w in edge_weights.items():
        G.add_edge(a, b, weight=w)
    return G


def compute_tfidf(doc_terms: Dict[str, List[str]]) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    N = len(doc_terms)
    doc_freq = Counter()
    for source, terms in doc_terms.items():
        for t in set(terms):
            doc_freq[t] += 1
    tfidf_vectors = {}
    for source, terms in doc_terms.items():
        tf = Counter(terms)
        max_tf = max(tf.values()) if tf else 1
        vec = {}
        for t, cnt in tf.items():
            tf_norm = cnt / max_tf
            idf = math.log((N + 1) / (doc_freq.get(t, 0) + 1)) + 1
            vec[t] = tf_norm * idf
        tfidf_vectors[source] = vec
    return dict(doc_freq), tfidf_vectors


# === Embedding ===

def compute_embeddings(docs: List[Dict], existing_embeddings: Optional[dict] = None) -> Tuple[np.ndarray, List[str], dict]:
    """
    Compute sentence-transformers embeddings for documents.
    Returns (embeddings_array, source_list, embedding_meta)
    """
    from sentence_transformers import SentenceTransformer
    
    model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    
    sources = []
    texts_to_embed = []
    embed_meta = {}
    
    if existing_embeddings is None:
        existing_embeddings = {}
    
    for doc in docs:
        src = doc["source"]
        if src in existing_embeddings:
            sources.append(src)
            embed_meta[src] = {"cached": True}
            continue
        sources.append(src)
        texts_to_embed.append(src)  # We'll embed source+text
        embed_meta[src] = {"cached": False}
    
    # Build full texts for embedding
    full_texts = []
    existing_indices = []
    new_indices = []
    
    for i, doc in enumerate(docs):
        src = doc["source"]
        if src in existing_embeddings:
            existing_indices.append(i)
        else:
            new_indices.append(i)
            # Create a rich text for embedding: source + key content
            text = doc["text"]
            full_texts.append(text[:1024])  # Limit to 1024 chars for speed
    
    n_total = len(docs)
    embeddings = np.zeros((n_total, 384), dtype=np.float32)
    
    # Copy existing embeddings
    if existing_indices:
        for idx in existing_indices:
            src = docs[idx]["source"]
            if src in existing_embeddings:
                embeddings[idx] = existing_embeddings[src]
    
    # Compute new embeddings
    if new_indices:
        print(f"   Computing embeddings for {len(new_indices)} new docs...", flush=True)
        new_embs = model.encode(full_texts, normalize_embeddings=True, show_progress_bar=True)
        for j, idx in enumerate(new_indices):
            embeddings[idx] = new_embs[j]
    
    return embeddings, [d["source"] for d in docs], embed_meta


def load_or_compute_embeddings(docs: List[Dict], force: bool = False) -> Tuple[Optional[np.ndarray], Optional[List[str]]]:
    """Load cached embeddings or compute if needed. Returns (embeddings, sources) or (None, None)."""
    if not force and os.path.exists(EMBEDDING_FILE) and os.path.exists(EMBEDDING_META_FILE):
        try:
            with open(EMBEDDING_META_FILE) as f:
                meta = json.load(f)
            sources = meta.get("sources", [])
            # Check if sources match
            if len(sources) == len(docs) and all(sources[i] == docs[i]["source"] for i in range(len(docs))):
                embeddings = np.load(EMBEDDING_FILE)
                print(f"   Loaded {len(embeddings)} cached embeddings from {EMBEDDING_FILE}")
                return embeddings, sources
            else:
                print(f"   Embedding cache stale ({len(sources)} cached vs {len(docs)} current)")
        except Exception as e:
            print(f"   Embedding cache load failed: {e}")
    
    # Need to compute
    if not has_sentence_transformers():
        print("   sentence-transformers not available, skipping embeddings")
        return None, None
    
    existing = {}
    if os.path.exists(EMBEDDING_FILE) and os.path.exists(EMBEDDING_META_FILE) and not force:
        try:
            old_embs = np.load(EMBEDDING_FILE)
            with open(EMBEDDING_META_FILE) as f:
                old_meta = json.load(f)
            old_sources = old_meta.get("sources", [])
            for i, s in enumerate(old_sources):
                if i < len(old_embs):
                    # Find matching doc
                    for doc in docs:
                        if doc["source"] == s:
                            existing[s] = old_embs[i]
                            break
        except Exception:
            pass
    
    embeddings, sources, _ = compute_embeddings(docs, existing)
    # Save
    np.save(EMBEDDING_FILE, embeddings)
    with open(EMBEDDING_META_FILE, "w") as f:
        json.dump({"sources": sources}, f, ensure_ascii=False)
    print(f"   Saved {len(embeddings)} embeddings to {EMBEDDING_FILE}")
    return embeddings, sources


def has_sentence_transformers() -> bool:
    try:
        import sentence_transformers
        return True
    except ImportError:
        return False


# === TF-IDF Cosine Similarity Search ===

def search_tfidf(query: str, top_k: int = 10) -> List[Dict]:
    """
    TF-IDF cosine similarity search.
    Uses existing TF-IDF vectors and inverted index (rev_index.db).
    Returns ranked documents with scores.
    """
    # Load data
    if not os.path.exists(TFIDF_FILE) or not os.path.exists(TERMS_FILE):
        print("TF-IDF data not found. Run `hipporag-lite.py index` first.")
        return []
    
    with open(TFIDF_FILE) as f:
        tfidf_vectors = json.load(f)
    with open(TERMS_FILE) as f:
        doc_freq = json.load(f)
    
    # Tokenize query
    query_tokens = tokenize(query)
    if not query_tokens:
        print("  No meaningful tokens in query")
        return []
    
    print(f"  Query tokens: {query_tokens}")
    
    # Build query TF-IDF vector
    N = len(tfidf_vectors)
    query_tf = Counter(query_tokens)
    max_tf = max(query_tf.values())
    
    query_vec = {}
    for t, cnt in query_tf.items():
        tf_norm = cnt / max_tf
        idf = math.log((N + 1) / (doc_freq.get(t, 0) + 1)) + 1
        query_vec[t] = tf_norm * idf
    
    q_norm = math.sqrt(sum(v * v for v in query_vec.values()))
    if q_norm == 0:
        return []
    
    # Normalize query vector
    for t in query_vec:
        query_vec[t] /= q_norm
    
    # Find candidate docs via rev_index.db
    candidate_scores = defaultdict(float)
    nb_term_matches = {}  # {source: matched_terms}
    
    if os.path.exists(REV_DB_FILE):
        rev_conn = sqlite3.connect(REV_DB_FILE)
        for term in query_tokens:
            cur = rev_conn.execute("SELECT source FROM term_sources WHERE term=?", (term,))
            for row in cur.fetchall():
                source = row[0]
                if source in tfidf_vectors:
                    # Pre-score by dot product of query term * doc term
                    if source not in nb_term_matches:
                        nb_term_matches[source] = set()
                    nb_term_matches[source].add(term)
                    doc_vec = tfidf_vectors[source]
                    if term in doc_vec:
                        candidate_scores[source] += query_vec[term] * doc_vec[term]
        rev_conn.close()
    
    if not candidate_scores:
        print("  No candidate documents found for query tokens")
        return []
    
    # Compute full cosine similarity with normalization
    final_scores = {}
    for source, score in candidate_scores.items():
        doc_vec = tfidf_vectors[source]
        d_norm = math.sqrt(sum(v * v for v in doc_vec.values()))
        if d_norm > 0:
            final_scores[source] = score / d_norm
    
    # Rank
    ranked = sorted(final_scores.items(), key=lambda x: -x[1])
    
    # Build results
    results = []
    for source, score in ranked[:top_k]:
        results.append({
            "source": source,
            "score": round(score, 4),
            "matched_terms": len(nb_term_matches.get(source, set())),
            "doc_type": "skill" if source.startswith("skill:") else "session",
            "name": source.replace("skill:", "").replace("session:", ""),
        })
    
    return results


# === Embedding-based Semantic Search ===

def search_embedding(query: str, top_k: int = 10) -> List[Dict]:
    """
    Semantic search using sentence-transformers embeddings.
    """
    if not has_sentence_transformers():
        print("  sentence-transformers not installed. Install with: pip install sentence-transformers")
        return search_tfidf(query, top_k)
    
    if not os.path.exists(EMBEDDING_FILE) or not os.path.exists(EMBEDDING_META_FILE):
        print("  Embeddings not found. Run `hipporag-lite.py index --embed` first.")
        print("  Falling back to TF-IDF search...")
        return search_tfidf(query, top_k)
    
    from sentence_transformers import SentenceTransformer
    
    # Load embeddings
    embeddings = np.load(EMBEDDING_FILE)
    with open(EMBEDDING_META_FILE) as f:
        meta = json.load(f)
    sources = meta.get("sources", [])
    
    if len(embeddings) != len(sources):
        print(f"  Embedding mismatch: {len(embeddings)} embs vs {len(sources)} sources")
        return search_tfidf(query, top_k)
    
    # Embed query
    model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    q_emb = model.encode(query, normalize_embeddings=True)
    
    # Cosine similarity (already normalized)
    scores = embeddings @ q_emb
    
    # Top-K
    top_indices = np.argsort(scores)[::-1][:top_k]
    
    results = []
    for idx in top_indices:
        source = sources[idx]
        score = float(scores[idx])
        if score < 0.1:  # Low threshold
            continue
        results.append({
            "source": source,
            "score": round(score, 4),
            "matched_terms": 0,
            "doc_type": "skill" if source.startswith("skill:") else "session",
            "name": source.replace("skill:", "").replace("session:", ""),
        })
    
    return results


# === Format Results ===

def format_results(results: List[Dict], query: str, mode: str = "tfidf") -> str:
    if not results:
        return f"🔍 По запросу «{query}» ничего не найдено."
    
    mode_label = "PPR (граф ассоциаций)" if mode == "ppr" else ("семантический" if mode == "embed" else "TF-IDF")
    lines = [
        f"🔍 **{query}** — {len(results)} результатов ({mode_label})\n"
    ]
    
    for i, r in enumerate(results, 1):
        score_display = f"score: {r['score']}"
        if r['matched_terms'] > 0:
            score_display += f", terms: {r['matched_terms']}"
        
        source = r['source']
        name = r['name']
        
        lines.append(f"{i}. **{name}** ({score_display})")
        
        # Show source type
        if r['doc_type'] == 'skill':
            lines.append(f"   📋 {source}")
        else:
            lines.append(f"   💬 {source}")
        
        lines.append("")
    
    return "\n".join(lines)


# === Indexing ===

def index_incremental(force_full: bool = False, compute_embeddings_flag: bool = False):
    print("=== HippoRAG Lite v3 — Indexing ===\n")

    print("1. Collecting documents...")
    docs = collect_documents_with_mtime()
    print(f"   {len(docs)} documents collected")

    manifest = load_manifest()

    if force_full or not manifest:
        print("2. Full reindex\n")
        changed_docs = docs
    else:
        changed_docs, _, removed_sources = detect_changed_files(docs, manifest)
        print(f"   Changed: {len(changed_docs)}, Removed: {len(removed_sources)}")
        if len(changed_docs) > len(docs) * 0.5:
            print(f"   >50% changed — full reindex")
            changed_docs = docs

    # Токенизация
    print("\n2. Tokenizing...", flush=True)
    doc_terms = {}
    for i, doc in enumerate(docs):
        tokens = tokenize(doc["text"])
        if tokens:
            doc_terms[doc["source"]] = tokens
        if i > 0 and i % 1500 == 0:
            print(f"     [{i}/{len(docs)}] tokenized...", flush=True)

    total_terms = sum(len(t) for t in doc_terms.values())
    unique_terms = set(t for v in doc_terms.values() for t in v)
    print(f"   {len(doc_terms)} docs with terms, {total_terms} total tokens, ~{len(unique_terms)} unique")

    # TF-IDF
    print("3. Computing TF-IDF...", flush=True)
    doc_freq, tfidf_vectors = compute_tfidf(doc_terms)
    print(f"   {len(doc_freq)} unique terms, {len(tfidf_vectors)} vectors")

    # Co-occurrence граф (legacy — сохраняем для обратной совместимости)
    print("4. Building co-occurrence graph (legacy)...", flush=True)
    G = build_cooc_graph(doc_terms)
    print(f"   Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    with open(GRAPH_FILE, "wb") as f:
        pickle.dump(G, f)
    
    # Sparse matrix (legacy)
    try:
        node_list = list(G.nodes())
        node_idx = {n: i for i, n in enumerate(node_list)}
        rows, cols, data = [], [], []
        for u, v, d in G.edges(data=True):
            i, j = node_idx[u], node_idx[v]
            rows.append(i); cols.append(j); data.append(d.get('weight', 1))
        P = csr_matrix((data, (rows, cols)), shape=(len(node_list), len(node_list)), dtype=np.float64)
        col_sums = np.array(P.sum(axis=0)).flatten()
        col_sums[col_sums == 0] = 1
        P = P @ csr_matrix(np.diag(1.0 / col_sums))
        save_npz(SPARSE_MATRIX_FILE, P)
        with open(NODE_LIST_FILE, "w") as f:
            json.dump(node_list, f)
        print(f"   Sparse matrix saved ({P.nnz} non-zeros)")
    except Exception as e:
        print(f"   Sparse matrix save skipped: {e}")
    
    # Save documents metadata
    print("5. Saving metadata...")
    with open(DOCS_FILE, "w") as f:
        json.dump([{"source": d["source"], "type": d["type"]} for d in docs], f)
    
    # Reverse index (для быстрого поиска по термам)
    if os.path.exists(REV_DB_FILE):
        os.remove(REV_DB_FILE)
    conn = sqlite3.connect(REV_DB_FILE)
    conn.execute("CREATE TABLE term_sources (term TEXT, source TEXT)")
    conn.execute("CREATE INDEX idx_term ON term_sources(term)")
    for source, vec in tfidf_vectors.items():
        terms_done = set()
        for term in vec:
            if term not in terms_done:
                conn.execute("INSERT INTO term_sources VALUES (?, ?)", (term, source))
                terms_done.add(term)
    conn.commit()
    conn.close()
    
    with open(TERMS_FILE, "w") as f:
        json.dump(doc_freq, f, ensure_ascii=False)
    with open(TFIDF_FILE, "w") as f:
        json.dump(tfidf_vectors, f, ensure_ascii=False)

    new_manifest = {d["source"]: d["mtime"] for d in docs}
    save_manifest(new_manifest)

    meta = {
        "total_docs": len(doc_terms),
        "total_terms": len(doc_freq),
        "graph_nodes": G.number_of_nodes(),
        "graph_edges": G.number_of_edges(),
        "changed_docs": len(changed_docs),
    }
    
    # Embeddings (опционально)
    if compute_embeddings_flag and has_sentence_transformers():
        print("\n6. Computing embeddings...", flush=True)
        embeddings, sources = load_or_compute_embeddings(docs, force=force_full)
        if embeddings is not None:
            meta["has_embeddings"] = True
            meta["embedding_dim"] = embeddings.shape[1]
            print(f"   Embeddings: {embeddings.shape}")
        else:
            meta["has_embeddings"] = False
    else:
        meta["has_embeddings"] = False
    
    with open(META_FILE, "w") as f:
        json.dump(meta, f)

    print(f"\n✓ Done! {len(doc_freq)} unique terms, {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    if meta.get("has_embeddings"):
        print(f"  ✓ Embeddings computed and cached")
    print()
    
    # Print search instructions
    print("Поиск:")
    print("  hipporag-lite.py search <query>           — TF-IDF поиск")
    print("  hipporag-lite.py search --embed <query>   — семантический поиск")


# === PPR (Personal PageRank) Search ===

def search_ppr(query: str, top_k: int = 10) -> List[Dict]:
    """
    Personal PageRank search on co-occurrence graph.
    Finds terms associatively related to query via graph structure,
    then maps back to documents (sessions/skills).

    Unlike TF-IDF (exact term match) and embeddings (semantic similarity),
    PPR finds structural connections: query "дивергенция" → finds sessions
    about RSI, MACD, цена even if those words don't appear together.
    """
    if not os.path.exists(SPARSE_MATRIX_FILE) or not os.path.exists(NODE_LIST_FILE):
        print("  PPR matrix not found. Run `hipporag-lite.py index` first.")
        return search_tfidf(query, top_k)

    # Load column-stochastic PPR matrix (P where columns sum to 1)
    # PPR uses P.T which is row-stochastic for the update: x_new = α·P.T·x + (1-α)·seed
    ppr_mat = load_npz(SPARSE_MATRIX_FILE)

    # Load node list
    with open(NODE_LIST_FILE) as f:
        node_list = json.load(f)
    
    if isinstance(node_list, list):
        term_to_idx = {t: i for i, t in enumerate(node_list)}
    else:
        print("  Unexpected node_list format")
        return []
    
    n_nodes = ppr_mat.shape[0]
    
    # Tokenize query
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    
    # Build seed vector: find query terms in graph
    seed = np.zeros(n_nodes, dtype=np.float64)
    seed_count = 0
    for qt in query_tokens:
        qt_lower = qt.lower()
        # Exact match
        if qt_lower in term_to_idx:
            seed[term_to_idx[qt_lower]] = 1.0
            seed_count += 1
        # Prefix / substring match (rsi → rsi_divergence)
        for term, idx in term_to_idx.items():
            if term.startswith(qt_lower) or qt_lower.startswith(term):
                if seed[idx] == 0:
                    seed[idx] = 0.5
                    seed_count += 1
    
    if seed_count == 0:
        print("  No query terms found in co-occurrence graph. Try TF-IDF search.")
        return []
    
    # Normalize seed vector
    seed = seed / seed.sum()
    
    # PPR iteration
    # x_{t+1} = α · P.T · x_t + (1-α) · seed
    alpha = 0.85
    damping = (1 - alpha)
    x = np.zeros(n_nodes, dtype=np.float64)
    ppr_t = ppr_mat.T.tocsr()  # transpose once
    for _ in range(30):
        x_new = damping * seed + alpha * (ppr_t @ x)
        if np.linalg.norm(x_new - x) < 1e-8:
            break
        x = x_new
    
    # Get top terms by PPR score
    top_indices = np.argsort(-x)
    
    # Map PPR-ranked terms to documents using rev_index.db (term → docs)
    # and score documents by PPR-weighted term matches
    doc_scores = defaultdict(float)
    doc_matched = defaultdict(set)
    if os.path.exists(REV_DB_FILE):
        rev_conn = sqlite3.connect(REV_DB_FILE)
        for rank_pos in range(min(100, len(top_indices))):
            term_idx = top_indices[rank_pos]
            term = node_list[term_idx]
            ppr_score = x[term_idx]
            if ppr_score < 0.001:
                continue
            try:
                cur = rev_conn.execute("SELECT source FROM term_sources WHERE term=?", (term,))
                for row in cur.fetchall():
                    src = row[0]
                    doc_scores[src] += ppr_score * 10  # weight by PPR score
                    doc_matched[src].add(term)
            except Exception:
                pass
        rev_conn.close()
    
    if not doc_scores:
        return []
    
    # Rank
    ranked = sorted(doc_scores.items(), key=lambda kv: -kv[1])
    
    # Build results
    results = []
    for source, score in ranked[:top_k]:
        results.append({
            "source": source,
            "score": round(score, 4),
            "matched_terms": len(doc_matched.get(source, set())),
            "doc_type": "skill" if source.startswith("skill:") else "session",
            "name": source.replace("skill:", "").replace("session:", ""),
            "method": "ppr",
        })
    
    return results


# === Search (Main entry point) ===

def search(query: str, top_k: int = 10, use_embedding: bool = False, use_ppr: bool = False) -> List[Dict]:
    """
    Main search entry point.
    - TF-IDF cosine similarity (default)
    - Embedding-based if use_embedding=True
    - PPR (Personal PageRank) if use_ppr=True
    - Hybrid: PPR + embedding + TF-IDF merged if --hybrid
    """
    if use_ppr:
        return search_ppr(query, top_k)
    if use_embedding:
        return search_embedding(query, top_k)
    return search_tfidf(query, top_k)


# === CLI ===

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "index":
        force_full = "--full" in sys.argv or "--force" in sys.argv
        compute_emb = "--embed" in sys.argv
        index_incremental(force_full=force_full, compute_embeddings_flag=compute_emb)
    
    elif len(sys.argv) > 1 and sys.argv[1] == "search":
        use_embed = "--embed" in sys.argv
        use_ppr_flag = "--ppr" in sys.argv
        use_hybrid = "--hybrid" in sys.argv
        # Remove flags from args
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        query = " ".join(args) if args else sys.stdin.read().strip()
        if query:
            if use_hybrid:
                # Hybrid: run all 3, merge results
                print(f"=== Гибридный поиск (TF-IDF + Embedding + PPR) для: «{query}» ===\n")
                res_tfidf = search_tfidf(query, top_k=5)
                res_emb = search_embedding(query, top_k=5)
                res_ppr = search_ppr(query, top_k=5)
                # Merge with dedup (keep best score per source)
                merged = {}
                for r in res_tfidf:
                    merged[r["source"]] = {"score": r["score"], "method": "tfidf", "source": r["source"]}
                for r in res_emb:
                    src = r["source"]
                    if src not in merged or merged[src]["score"] < r["score"]:
                        merged[src] = {"score": r["score"], "method": "embed", "source": src}
                for r in res_ppr:
                    src = r["source"]
                    if src not in merged or merged[src]["score"] < r["score"]:
                        merged[src] = {"score": r["score"], "method": "ppr", "source": src}
                ranked = sorted(merged.values(), key=lambda x: -x["score"])[:10]
                print(f"{'Source':50s} {'Score':8s} {'Method':8s}")
                print("-"*70)
                for r in ranked:
                    name = r["source"].replace("session:", "").replace("skill:", "")[:48]
                    print(f"{name:50s} {r['score']:.4f}  {r['method']:8s}")
            else:
                results = search(query, use_embedding=use_embed, use_ppr=use_ppr_flag)
                mode = "ppr" if use_ppr_flag else ("embed" if use_embed else "tfidf")
                print(format_results(results, query, mode=mode))
        else:
            print("Usage: hipporag-lite.py search [--embed|--ppr|--hybrid] <query>")
    
    elif len(sys.argv) > 1 and sys.argv[1] == "compare":
        """Сравнение TF-IDF + Embedding + PPR поиска"""
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        query = " ".join(args) if args else sys.stdin.read().strip()
        if query:
            print(f"=== Сравнение методов поиска для: «{query}» ===\n")
            
            print("--- TF-IDF Cosine Similarity ---")
            results_tfidf = search_tfidf(query)
            print(format_results(results_tfidf, query, mode="tfidf"))
            
            print("\n--- Embedding Semantic Search ---")
            results_emb = search_embedding(query)
            print(format_results(results_emb, query, mode="embed"))
            
            print("\n--- PPR (Personal PageRank) ---")
            results_ppr = search_ppr(query)
            print(format_results(results_ppr, query, mode="ppr"))
        else:
            print("Usage: hipporag-lite.py compare <query>")
    
    elif len(sys.argv) > 1 and sys.argv[1] == "seed-test":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else sys.stdin.read().strip()
        if query:
            qt = tokenize(query)
            print(f"Query tokens: {qt}")
            if os.path.exists(GRAPH_FILE):
                import networkx as nx
                with open(GRAPH_FILE, "rb") as f:
                    G = pickle.load(f)
                nodes_in = [t for t in qt if t in G]
                print(f"In graph: {nodes_in}")
                for n in nodes_in:
                    print(f"  {n}: {list(G.neighbors(n))[:10]}")
            else:
                print("Graph not found")
    
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        """Show index statistics"""
        print("=== HippoRAG Lite v3 — Statistics ===\n")
        if os.path.exists(META_FILE):
            with open(META_FILE) as f:
                meta = json.load(f)
            for k, v in meta.items():
                print(f"  {k}: {v}")
        else:
            print("  No index found. Run `hipporag-lite.py index` first.")
        
        if os.path.exists(EMBEDDING_FILE):
            embs = np.load(EMBEDDING_FILE)
            print(f"\n  Embeddings: {embs.shape}")
        else:
            print("\n  Embeddings: not computed (run with --embed)")
    
    else:
        print("Usage:")
        print("  hipporag-lite.py index [--full] [--embed]  — индексация")
        print("  hipporag-lite.py search [--embed] <query>  — поиск (TF-IDF или embedding)")
        print("  hipporag-lite.py compare <query>            — сравнение методов")
        print("  hipporag-lite.py seed-test <query>          — отладка токенизации")
        print("  hipporag-lite.py stats                      — статистика индекса")

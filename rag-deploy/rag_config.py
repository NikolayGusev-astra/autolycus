"""
RAG Configuration — централизованные настройки для Agentic RAG

Модели:
- e5-large-instruct — embedding (encoder-only, 1024d, мультиязычная)
- gemma-4-e4b — классификация запросов (factual/analytical/synthesis)
- qwen2.5-7b-instruct — query rewriting/expansion (7B, инструментальная)
- cosine threshold — вместо LLM grading (ни одна локальная модель не подходит)

Fallback: owl-alpha через OpenRouter для сложных случаев.
"""
import os

# Embedding API
EMBEDDING_URL = os.getenv("RAG_EMBEDDING_URL", "http://localhost:1234/v1/embeddings")
EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-multilingual-e5-large-instruct")
EMBEDDING_DIM = 1024

# LLM для классификации запросов (простая задача: 3 класса, короткий ответ)
LLM_CLASSIFY_URL = os.getenv("RAG_LLM_CLASSIFY_URL", "http://localhost:1234/v1/chat/completions")
LLM_CLASSIFY_MODEL = os.getenv("RAG_LLM_CLASSIFY_MODEL", "google/gemma-4-e4b")

# LLM для query rewriting (переформулировка, требует понимания намерения)
LLM_REWRITE_URL = os.getenv("RAG_LLM_REWRITE_URL", "http://localhost:1234/v1/chat/completions")
LLM_REWRITE_MODEL = os.getenv("RAG_LLM_REWRITE_MODEL", "qwen2.5-7b-instruct")

# ChromaDB
CHROMA_PATH = os.getenv("RAG_CHROMA_PATH", os.path.expanduser("~/.cache/chroma"))
COLLECTION_NAME = "wiki"

# Chunking
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 100
MAX_CHARS_PER_INPUT = 2500

# Search
DEFAULT_K = 5
RELEVANCE_THRESHOLD = 0.3

# Grading: cosine threshold вместо LLM
# Ни одна локальная модель не подходит для binary classification чанков
# Gemma 4B отклоняет 95% релевантных чанков, Qwen 7B тоже не идеальна
COSINE_THRESHOLD = 0.7  # чанки с cosine < 0.7 отбрасываются

# Source-type boost (мягкий, без clamp до 1.0)
SOURCE_BOOST = {
    "concepts/":   0.05,
    "adr/":        0.03,
    "plans/":      0.03,
    "manuals/":    0.03,
}

# Wiki paths
WIKI_PATHS = [
    os.path.expanduser("/root/wiki"),
    os.path.expanduser("/root/llm-wiki"),
]

# Exclude directories from indexing (noise reduction)
# Исключаем: сессии, логи, дайджесты импортов, переписки — всё что не является
# структурированными знаниями (concepts/, adr/, plans/, manuals/, entities/)
# НЕ исключаем: raw/auto-findings (полезные находки из сессий)
EXCLUDE_DIRS = [
    "queries",              # mempalace query logs
    "session-notes",        # session notes (raw logs)
    "wiki/log*",            # лог вики
    "raw/search_*",         # search result dumps
    "raw/import-digest*",   # дайджесты импортов (telegram/cli переписка)
    "raw/product/meeting*", # переписки из TG/CLI с датами
    "concepts/202605*-",    # экспортированные сессии из TG/CLI
    "concepts/202606*-",    # экспортированные сессии из TG/CLI
]

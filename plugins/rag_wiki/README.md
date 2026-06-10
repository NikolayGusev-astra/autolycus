# RAG Wiki Search Plugin

Автоматический семантический поиск по wiki через ChromaDB с инжекцией контекста в каждый запрос агента.

## Что делает

**pre_llm_call hook** — вызывается автоматически перед каждым LLM запросом:
1. Берёт сообщение пользователя
2. Запускает семантический поиск по wiki (ChromaDB)
3. Фильтрует шум (auto-findings, queries, session-notes, score < 0.5)
4. Инжектит релевантные чанки в user message (не в system prompt — сохраняет prompt cache)

**Инструменты:**
- `rag_search` — поиск по запросу (on-demand)
- `rag_index` — переиндексация (incremental / full / clear)

## Установка

1. Файлы плагина: `plugins/rag_wiki/`
2. RAG скрипты: `/root/rag-deploy/`
3. Добавить `rag-wiki` в `plugins.enabled` конфига

```yaml
plugins:
  enabled: ["sbl", "rtk", "rag-wiki"]
```

## Конфигурация (env vars)

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `RAG_QUERY_SCRIPT` | `/root/rag-deploy/rag_query.py` | Путь к query helper |
| `RAG_INDEX_SCRIPT` | `/root/rag-deploy/rag_indexer.py` | Путь к индексатору |
| `RAG_DEFAULT_K` | `5` | Количество результатов по умолчанию |
| `RAG_MAX_CONTEXT_CHARS` | `4000` | Макс. размер инжектируемого контекста |

## Фильтрация шума

Исключаемые директории (rag_config.py → EXCLUDE_DIRS):
- `queries/` — mempalace query logs
- `raw/auto-findings/` — auto-ingested session findings
- `raw/search_*` — search result dumps
- `session-notes/` — session notes

Дополнительная фильтрация в хуке:
- Score < 0.5 отбрасывается
- Источники из `NOISE_PATTERNS` отбрасываются

## Cron

Авто-индексация каждый час:
```
0 * * * * cd /root/rag-deploy && python3 rag_indexer.py --incremental
```

## Зависимости

```bash
pip3 install --break-system-packages chromadb pyyaml requests
```

## Технические детали

- **ChromaDB**: `~/.cache/chroma/wiki`
- **Embedding**: `text-embedding-multilingual-e5-large-instruct` (1024d) @ localhost:1234
- **LLM для agentic**: `google/gemma-4-e4b:2` @ localhost:1234
- **Wiki paths**: `/root/wiki` (2000+ .md), `/root/llm-wiki` (5 .md)
- **Chunking**: по заголовкам ##/###, 2000 chars max, overlap 100

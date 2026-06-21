# RAG Wiki Search Plugin (Zvec edition)

Автоматический семантический поиск по wiki через **Zvec** (FTS + векторный поиск) с инжекцией контекста в каждый запрос агента.

## Что делает

**pre_llm_call hook** — вызывается автоматически перед каждым LLM запросом:
1. Берёт сообщение пользователя
2. Запускает FTS-поиск по Zvec коллекции вики (`~/.cache/zvec/wiki`)
3. Фильтрует шум (auto-findings, queries, session-notes)
4. Возвращает `{"context": "..."}` — релевантные чанки инжектятся в user message
   (не в system prompt — сохраняет prompt cache)

## Установка

1. Файлы плагина: `plugins/rag_wiki/`
2. Zvec коллекция: `~/.cache/zvec/wiki`
3. `rag-wiki` добавлен в `plugins.enabled` конфига

```yaml
plugins:
  enabled:
    - rag-wiki
```

## Конфигурация (env vars)

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `RAG_ZVEC_PATH` | `~/.cache/zvec/wiki` | Путь к Zvec коллекции |
| `RAG_WIKI_MIN_SCORE` | `0.0` | Минимальный score FTS |
| `RAG_WIKI_TOP_K` | `8` | Количество результатов |
| `RAG_WIKI_MAX_CONTEXT_LEN` | `1500` | Макс. символов на чанк |
| `WIKI_DIR` | `/root/wiki` | Путь к вики файлам |

## Фильтрация шума

Исключаемые директории:
- `queries/` — mempalace query logs
- `raw/auto-findings/` — auto-ingested session findings
- `raw/search_*` — search result dumps
- `session-notes/` — session notes

## Скрипты

| Скрипт | Назначение |
|--------|-----------|
| `scripts/zvec_rag_query.py` | FTS-поиск по Zvec коллекции |
| `scripts/zvec_rag_embed.py` | Инкрементальная индексация |

## Индексация

Cron job (каждый час):
```
0 * * * * python3 /opt/autolycus/plugins/rag_wiki/scripts/zvec_rag_embed.py
```

Или через Hermes cron:
```
cronjob(action="create", schedule="0 * * * *",
  prompt="Запуск инкрементальной индексации вики",
  skills=["rag-wiki"])
```

## API

```python
from rag_wiki import search, format_results

results = search("форд эксплорер полуось")
# → [{"title", "path", "content", "score", "source", "tags", "category"}]

context = format_results(results)
# → "### Title\nИсточник: ...\n\nContent..."
```

## Зависимости

- Zvec (установлен в `/opt/autolycus/venv/`)
- Python 3.12+

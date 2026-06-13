# Telegram Rich Messages

Дата: 2026-06-13

## Что это

Поддержка Telegram Rich Messages — нового API `sendRichMessage`, который
позволяет отправлять блочные сообщения: заголовки, списки, таблицы,
цитаты, collapsible-блоки (`<details>`), формулы LaTeX и медиа — всё
в одном сообщении до 32 768 UTF-8 символов (против 4 096 у обычных).

## Возможности

| Функция | Обычные сообщения | Rich Messages |
|---------|-------------------|---------------|
| Лимит | 4 096 UTF-16 | 32 768 UTF-8 |
| Таблицы | Нет (костыль → row groups) | Нативные GFM |
| Списки | Нет | Ordered, unordered, task lists |
| Заголовки | `*bold*` | `# H1` — `###### H6` |
| Blockquote | `> text` | `> text` + `<aside>` pull quote |
| Collapsible | Нет | `<details>` блоки |
| Формулы | Нет | `$x^2$`, `$$E=mc^2$$` |
| Сноски | Нет | `[^1]: footnote` |
| Медиа в тексте | Отдельная отправка | Inline блоки |
| Streaming | `edit_message_text` | `sendRichMessageDraft` |

## Конфигурация

В `config.yaml`:

```yaml
telegram:
  rich_messages: true   # default: false
```

Флаг opt-in. При `false` — ничего не меняется, обычный путь через
`send_message` + MarkdownV2.

## Архитектура

### Файлы

- `gateway/platforms/telegram_rich.py` — конвертер `format_rich_markdown()`
- `gateway/platforms/telegram.py` — методы `send_rich_message()`, `send_rich_draft()`
- `gateway/stream_consumer.py` — routing rich draft в стриминге
- `tests/gateway/test_telegram_rich_messages.py` — 17 тестов

### API вызовы

Используется `bot._post(endpoint, data)` — raw API метод PTB 22.6.
Обёрток `sendRichMessage` в библиотеке нет, но endpoint доступен через
`_post`.

### Fallback

При ошибке `sendRichMessage` автоматически откатывается на `send()` —
обычный MarkdownV2 путь. При ошибке `sendRichMessageDraft` — откат
на `send_draft` / `edit_message`.

### Конвертер

`format_rich_markdown(content)` — минимальная трансформация:
- GFM markdown проходит как есть (Rich Markdown совместим с GFM)
- Снимаются MarkdownV2 escape-sequences (`\_` → `_`) на случай если
  контент пришёл из legacy-пути

## Лимиты Rich Messages

- 32 768 UTF-8 символов
- 500 блоков (включая вложенные)
- 16 уровней вложенности
- 50 медиа вложений
- 20 колонок в таблице

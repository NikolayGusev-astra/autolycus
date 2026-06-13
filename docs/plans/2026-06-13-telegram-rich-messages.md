# Telegram Rich Messages — Implementation Plan

> **Для Autolycus:** Реализовать по задачам. Каждая задача = коммит.

**Цель:** Добавить нативную поддержку Telegram Rich Messages (`sendRichMessage`) в gateway — блочные сообщения с таблицами, списками, collapsible-блоками, формулами и лимитом 32K вместо 4K.

**Архитектура:** Новый метод `send_rich_message` в `TelegramAdapter` через `bot._post('sendRichMessage', data)`. Конвертер `markdown → rich_markdown` (почти identity, т.к. Rich Markdown совместим с GFM). Feature flag в config.yaml для opt-in. Обратная совместимость — fallback на `send_message` при ошибке.

**Tech Stack:** Python 3.12, python-telegram-bot 22.6 (`bot._post` для raw API), asyncio

---

## Задача 1: Конвертер markdown → rich_markdown

**Задача:** Функция `format_rich_markdown(content)` — конвертирует стандартный markdown агента в формат Rich Markdown Telegram.

**Файлы:**
- Создать: `gateway/platforms/telegram_rich.py` — модуль с конвертером

**Логика:**
Rich Markdown почти полностью совместим с GFM. Отличия от MarkdownV2:
- `**bold**` — **остаётся как есть** (не `*bold*`)
- `*italic*` — **остаётся как есть** (не `_italic_`)
- `~~strikethrough~~` — **остаётся как есть**
- `||spoiler||` — **остаётся как есть**
- `[text](url)` — **остаётся как есть**
- `` `code` `` и ` ```code``` ` — **остаётся как есть**
- `> blockquote` — **остаётся как есть**
- `## Headers` — **остаётся как есть** (нативно поддержаны!)
- Pipe tables `| a | b |` — **остаётся как есть** (нативные таблицы!)
- `- list` / `1. ordered` — **остаётся как есть**
- НЕТ нужды экранировать `_*[]()~` и т.д.

Что нужно сделать (минимальные преобразования):
1. Удалить escape-символы MarkdownV2 (`\_` → `_`, `\*` → `*`, и т.д.) — на случай если контент пришёл уже отформатированный
2. Передать как есть — GFM совместим

**Код:**

```python
"""Rich Markdown converter for Telegram Rich Messages.

Rich Markdown is GFM-compatible. This module handles edge cases where
content might contain MarkdownV2 escape sequences from the legacy path.
"""
import re


def format_rich_markdown(content: str) -> str:
    """Convert standard/GFM markdown to Telegram Rich Markdown.

    Rich Markdown (used by sendRichMessage) is compatible with GitHub
    Flavored Markdown. The main job here is to strip any MarkdownV2
    escape backslashes that might have been applied by the legacy
    format_message() path, since Rich Markdown does not need them.

    Args:
        content: Raw markdown text from the agent

    Returns:
        Text suitable for sendRichMessage's markdown field
    """
    if not content:
        return content

    text = content

    # Strip MarkdownV2 escape backslashes if present (content may have
    # been pre-processed by format_message before reaching us)
    text = re.sub(r'\\([_*\[\]()~`>#+\-=|{}.!\\])', r'\1', text)

    return text
```

**Верификация:**
```bash
python3 -c "
from gateway.platforms.telegram_rich import format_rich_markdown
# Basic markdown passes through
assert format_rich_markdown('**bold**') == '**bold**'
assert format_rich_markdown('| a | b |') == '| a | b |'
# MDv2 escapes are stripped
assert format_rich_markdown('hello \\_world\\_') == 'hello _world_'
print('OK')
"
```

**Коммит:** `feat(telegram): add rich markdown converter`

---

## Задача 2: Метод send_rich_message в TelegramAdapter

**Задача:** Добавить метод `send_rich_message` в класс `TelegramAdapter` через raw Bot API.

**Файлы:**
- Изменить: `gateway/platforms/telegram.py` — добавить метод в класс `TelegramAdapter`

**Логика:**
1. Использовать `self._bot._post('sendRichMessage', data)` для вызова нативного API
2. Принимать `content: str` (markdown) и опционально `html: str`
3. Поддержать `reply_to`, `metadata` (thread_id), `disable_notification`
4. Вернуть `SendResult` с `message_id`

**Код (добавить после метода `send` ~строка 2093):**

```python
async def send_rich_message(
    self,
    chat_id: str,
    content: str,
    *,
    use_html: bool = False,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> SendResult:
    """Send a rich message via Telegram's sendRichMessage API.

    Rich messages support GFM-compatible markdown, native tables, lists,
    blockquotes, details (collapsible), math formulas, and up to 32K chars.

    Falls back to regular send() if the API call fails.
    """
    if not self._bot:
        return SendResult(success=False, error="Not connected")

    if not content or not content.strip():
        return SendResult(success=True, message_id=None)

    try:
        from gateway.platforms.telegram_rich import format_rich_markdown

        thread_id = self._metadata_thread_id(metadata)
        reply_to_id = self._reply_to_message_id_for_send(
            reply_to, metadata, reply_to_mode=self._reply_to_mode
        )
        thread_kwargs = self._thread_kwargs_for_send(
            chat_id, thread_id, metadata,
            reply_to_message_id=reply_to_id,
            reply_to_mode=self._reply_to_mode,
        )

        rich_message: Dict[str, Any] = {}
        if use_html:
            rich_message["html"] = content
        else:
            rich_message["markdown"] = format_rich_markdown(content)

        data: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "rich_message": rich_message,
            **thread_kwargs,
            **self._notification_kwargs(metadata),
        }
        if reply_to_id is not None:
            data["reply_to_message_id"] = reply_to_id

        result = await self._bot._post("sendRichMessage", data)

        # Result is a Message-like dict; extract message_id
        msg_id = str(result.get("message_id", "")) if isinstance(result, dict) else None
        return SendResult(success=True, message_id=msg_id)

    except Exception as e:
        logger.warning(
            "[%s] sendRichMessage failed, falling back to send(): %s",
            self.name, e, exc_info=True,
        )
        # Fallback to regular send
        return await self.send(chat_id, content, reply_to=reply_to, metadata=metadata)
```

**Верификация:**
```bash
python3 -c "import py_compile; py_compile.compile('gateway/platforms/telegram.py', doraise=True); print('OK')"
```

**Коммит:** `feat(telegram): add send_rich_message method via raw Bot API`

---

## Задача 3: Feature flag в config

**Задача:** Добавить конфигурационную опцию `rich_messages` для включения Rich Messages.

**Файлы:**
- Изменить: `gateway/platforms/telegram.py` — чтение флага в `__init__`

**Логика:**
- `telegram.rich_messages: true/false` в config.yaml
- По умолчанию `false` (обратная совместимость)
- Если `true` — `send()` использует `send_rich_message` для финальных ответов

**Код (в `__init__`, рядом с `self._disable_link_previews`):**

```python
self._rich_messages: bool = self._coerce_bool_extra("rich_messages", False)
```

**В методе `send()` — перед блоком format_message/truncate, добавить проверку:**

```python
# Rich messages path: use sendRichMessage for longer, structured content
if getattr(self, "_rich_messages", False):
    # Rich messages support 32K chars — no need to split into 4K chunks
    result = await self.send_rich_message(
        chat_id, content, reply_to=reply_to, metadata=metadata,
    )
    if result.success:
        return result
    # Fallback continues below
```

**Верификация:**
```bash
python3 -c "import py_compile; py_compile.compile('gateway/platforms/telegram.py', doraise=True); print('OK')"
```

**Коммит:** `feat(telegram): add rich_messages config flag with send() routing`

---

## Задача 4: Rich Draft streaming (sendRichMessageDraft)

**Задача:** Добавить метод `send_rich_draft` для стриминга rich messages.

**Файлы:**
- Изменить: `gateway/platforms/telegram.py` — добавить метод

**Код (после `send_draft`):**

```python
async def send_rich_draft(
    self,
    chat_id: str,
    draft_id: int,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> SendResult:
    """Stream a partial rich message via sendRichMessageDraft.

    Animated preview that updates in-place when the same draft_id is reused.
    The final message must be sent via send_rich_message to persist.
    """
    if not self._bot:
        return SendResult(success=False, error="not_connected")

    try:
        from gateway.platforms.telegram_rich import format_rich_markdown

        thread_id = self._metadata_thread_id(metadata)

        data: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "draft_id": int(draft_id),
            "rich_message": {"markdown": format_rich_markdown(content)},
        }
        if thread_id is not None:
            data["message_thread_id"] = thread_id

        await self._bot._post("sendRichMessageDraft", data)
        return SendResult(success=True, message_id=None)
    except Exception as e:
        logger.debug("[%s] sendRichMessageDraft failed: %s", self.name, e)
        return SendResult(success=False, error=str(e))
```

**Коммит:** `feat(telegram): add send_rich_draft for rich message streaming`

---

## Задача 5: Интеграция в stream_consumer

**Задача:** Маршрутизировать стриминг через rich draft когда rich_messages включён.

**Файлы:**
- Изменить: `gateway/stream_consumer.py` — проверка флага adapter для rich draft

**Логика:**
В `_send_draft_frame()` (строка ~906) — если adapter имеет `send_rich_draft` и rich_messages включён, использовать его вместо `send_draft`.

**Код (модификация `_send_draft_frame`):**

```python
async def _send_draft_frame(self, text: str) -> bool:
    """Send a single draft frame via the adapter."""
    try:
        # Use rich draft if adapter supports it and rich mode is on
        if (
            hasattr(self.adapter, "send_rich_draft")
            and getattr(self.adapter, "_rich_messages", False)
        ):
            result = await self.adapter.send_rich_draft(
                chat_id=self._chat_id,
                draft_id=self._draft_id,
                content=text,
                metadata=self._metadata,
            )
        else:
            result = await self.adapter.send_draft(
                chat_id=self._chat_id,
                draft_id=self._draft_id,
                content=text,
                metadata=self._metadata,
            )
        if result.success:
            return True
        # ... existing failure handling
```

**Верификация:**
```bash
python3 -c "import py_compile; py_compile.compile('gateway/stream_consumer.py', doraise=True); print('OK')"
```

**Коммит:** `feat(stream): route draft streaming through rich draft when enabled`

---

## Задача 6: Базовый тест

**Задача:** Тест на конвертер и на mock send_rich_message.

**Файлы:**
- Создать: `tests/gateway/test_telegram_rich_messages.py`

**Код:**

```python
"""Tests for Telegram Rich Messages integration."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.platforms.telegram_rich import format_rich_markdown


class TestFormatRichMarkdown:
    def test_plain_text_passthrough(self):
        assert format_rich_markdown("Hello world") == "Hello world"

    def test_gfm_bold_unchanged(self):
        assert format_rich_markdown("**bold**") == "**bold**"

    def test_gfm_table_unchanged(self):
        table = "| H1 | H2 |\n|----|----|\n| a  | b  |"
        assert format_rich_markdown(table) == table

    def test_gfm_list_unchanged(self):
        lst = "- item 1\n- item 2"
        assert format_rich_markdown(lst) == lst

    def test_mdv2_escapes_stripped(self):
        assert format_rich_markdown("hello \\_world\\_") == "hello _world_"
        assert format_rich_markdown("test \\*bold\\*") == "test *bold*"

    def test_empty_input(self):
        assert format_rich_markdown("") == ""
        assert format_rich_markdown(None) is None

    def test_nested_formatting(self):
        text = "**bold _italic_ bold**"
        assert format_rich_markdown(text) == text

    def test_code_blocks(self):
        code = "```python\nprint('hello')\n```"
        assert format_rich_markdown(code) == code

    def test_blockquotes(self):
        quote = "> This is a quote"
        assert format_rich_markdown(quote) == quote
```

**Верификация:**
```bash
cd /opt/autolycus && source .venv/bin/activate
python3 -m pytest tests/gateway/test_telegram_rich_messages.py -v
```

**Коммит:** `test(telegram): add rich markdown converter tests`

---

## Задача 7: Документация + push

**Задача:** Описать фичу в `docs/` и запушить в репо.

**Файлы:**
- Создать: `docs/features/telegram-rich-messages.md`
- Включить `rich_messages: true` в `~/.autolycus/config.yaml` (на нашем сервере)

**Коммит:** `docs: add telegram rich messages feature documentation`

**Push:**
```bash
cd /root/autolycus/repo
# Cherry-pick коммитов из /opt/autolycus или просто скопировать изменённые файлы
git add -A
git commit -m "feat(telegram): Rich Messages support (sendRichMessage API)

- New: send_rich_message() via raw Bot API (_post)
- New: send_rich_draft() for streaming previews
- New: format_rich_markdown() converter (GFM-compatible)
- Config: telegram.rich_messages flag (default: false)
- Stream consumer: routes through rich draft when enabled
- 32K char limit (vs 4K), native tables/lists/blockquotes/details/math
- Fallback: send_message on API error"
git push origin main
```

"""Tests for WorkflowClassifier integration in Telegram adapter.

Covers:
- _build_message_event: workflow classifier fills auto_skill when no topic binding
- _build_message_event: topic binding takes priority over workflow classifier
- _build_message_event: low-confidence classification → no auto_skill set
- _build_message_event: empty/short text skipping
- _cached_clf shared across calls (no re-init per message)
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType


def _ensure_telegram_mock():
    """Ensure telegram module is mocked for isolated tests."""
    # Only set up mocks if telegram isn't installed
    try:
        import telegram  # noqa: F401
        return  # real module available, no need to mock
    except ImportError:
        pass

    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)

    constants_mod = MagicMock()
    constants_mod.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    constants_mod.ChatType.GROUP = "group"
    constants_mod.ChatType.SUPERGROUP = "supergroup"
    constants_mod.ChatType.CHANNEL = "channel"
    constants_mod.ChatType.PRIVATE = "private"

    sys.modules["telegram"] = telegram_mod
    sys.modules["telegram.ext"] = telegram_mod.ext
    sys.modules["telegram.constants"] = constants_mod
    sys.modules["telegram.request"] = telegram_mod.request
    sys.modules.pop("gateway.platforms.telegram", None)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


def _make_adapter(dm_topics_config=None, group_topics_config=None):
    """Create a TelegramAdapter with optional DM/group topics config."""
    # Reset cached classifier between tests to avoid state leak
    TelegramAdapter._workflow_classifier = None
    extra = {}
    if dm_topics_config is not None:
        extra["dm_topics"] = dm_topics_config
    if group_topics_config is not None:
        extra["group_topics"] = group_topics_config
    config = PlatformConfig(enabled=True, token="***", extra=extra)
    adapter = TelegramAdapter(config)
    return adapter


def _make_mock_message(chat_id=111, chat_type="private", text="hello", thread_id=None,
                       user_id=42, user_name="Test User", forum_topic_created=None,
                       is_topic_message=None, is_forum=None):
    """Create a mock Telegram Message for _build_message_event tests."""
    chat = SimpleNamespace(
        id=chat_id,
        type=chat_type,
        title=None,
    )
    if is_forum is not None:
        chat.is_forum = is_forum
    chat.full_name = user_name

    user = SimpleNamespace(
        id=user_id,
        full_name=user_name,
    )

    if is_topic_message is None:
        is_topic_message = bool(thread_id)

    msg = SimpleNamespace(
        chat=chat,
        from_user=user,
        text=text,
        message_thread_id=thread_id,
        is_topic_message=is_topic_message,
        message_id=1001,
        reply_to_message=None,
        date=None,
        forum_topic_created=forum_topic_created,
    )
    return msg


# ── Workflow classifier fills auto_skill when no topic binding ──


def test_workflow_classifier_fills_auto_skill_for_ford():
    """Ford-related query → auto_skill='auto-diagnostics'."""
    adapter = _make_adapter()
    msg = _make_mock_message(text="форд эксплорер не заводится")
    event = adapter._build_message_event(msg, MessageType.TEXT)
    assert event.auto_skill == "auto-diagnostics"


def test_workflow_classifier_fills_auto_skill_for_article():
    """Article-related query → auto_skill='autolycus-article-writer'."""
    adapter = _make_adapter()
    msg = _make_mock_message(text="напиши статью про агентов")
    event = adapter._build_message_event(msg, MessageType.TEXT)
    assert event.auto_skill == "autolycus-article-writer"


def test_workflow_classifier_fills_auto_skill_for_bitgn():
    """BitGN-related query → auto_skill='bitgn'."""
    adapter = _make_adapter()
    msg = _make_mock_message(text="что нового в bitgn ecom2")
    event = adapter._build_message_event(msg, MessageType.TEXT)
    assert event.auto_skill == "bitgn"


def test_workflow_classifier_no_match_for_greeting():
    """Generic greeting → auto_skill=None (no low-confidence assignment)."""
    adapter = _make_adapter()
    msg = _make_mock_message(text="привет")
    event = adapter._build_message_event(msg, MessageType.TEXT)
    assert event.auto_skill is None


def test_workflow_classifier_empty_text_skipped():
    """Empty text → auto_skill=None (classifier skipped)."""
    adapter = _make_adapter()
    msg = _make_mock_message(text="")
    event = adapter._build_message_event(msg, MessageType.TEXT)
    assert event.auto_skill is None


def test_workflow_classifier_short_text_skipped():
    """Very short text → auto_skill=None (classifier skipped)."""
    adapter = _make_adapter()
    msg = _make_mock_message(text="ок")
    event = adapter._build_message_event(msg, MessageType.TEXT)
    assert event.auto_skill is None


# ── Topic binding takes priority over workflow classifier ──


def test_topic_binding_overrides_workflow_classifier():
    """Explicit topic skill binding should take priority over workflow classification."""
    adapter = _make_adapter([
        {
            "chat_id": 111,
            "topics": [
                {"name": "My Project", "skill": "accessibility-auditor", "thread_id": 100},
            ],
        }
    ])
    adapter._dm_topics["111:My Project"] = 100

    # Text looks like Ford, but topic is bound to accessibility-auditor
    msg = _make_mock_message(chat_id=111, thread_id=100, text="форд эксплорер не заводится")
    event = adapter._build_message_event(msg, MessageType.TEXT)
    # Topic binding wins
    assert event.auto_skill == "accessibility-auditor"


# ── Structure checks: event still well-formed with classifier ──


def test_event_structure_with_workflow_auto_skill():
    """Event should have all standard fields when workflow sets auto_skill."""
    adapter = _make_adapter()
    msg = _make_mock_message(text="проверь форд эксплорер")
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill == "auto-diagnostics"
    assert event.text == "проверь форд эксплорер"
    assert event.message_type == MessageType.TEXT
    assert event.source is not None
    assert event.source.chat_id == "111"

"""Tests for Telegram Rich Messages integration."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.platforms.telegram_rich import format_rich_markdown


class TestFormatRichMarkdown:
    """Unit tests for the rich markdown converter."""

    def test_plain_text_passthrough(self):
        assert format_rich_markdown("Hello world") == "Hello world"

    def test_gfm_bold_unchanged(self):
        assert format_rich_markdown("**bold**") == "**bold**"

    def test_gfm_italic_unchanged(self):
        assert format_rich_markdown("*italic*") == "*italic*"

    def test_gfm_table_unchanged(self):
        table = "| H1 | H2 |\n|----|----|\n| a  | b  |"
        assert format_rich_markdown(table) == table

    def test_gfm_list_unchanged(self):
        lst = "- item 1\n- item 2"
        assert format_rich_markdown(lst) == lst

    def test_gfm_ordered_list_unchanged(self):
        lst = "1. first\n2. second"
        assert format_rich_markdown(lst) == lst

    def test_mdv2_escapes_stripped(self):
        assert format_rich_markdown("hello \\_world\\_") == "hello _world_"
        assert format_rich_markdown("test \\*bold\\*") == "test *bold*"
        assert format_rich_markdown("\\[link\\]\\(url\\)") == "[link](url)"

    def test_empty_input(self):
        assert format_rich_markdown("") == ""
        assert format_rich_markdown(None) is None

    def test_nested_formatting(self):
        text = "**bold _italic_ bold**"
        assert format_rich_markdown(text) == text

    def test_code_blocks_unchanged(self):
        code = "```python\nprint('hello')\n```"
        assert format_rich_markdown(code) == code

    def test_inline_code_unchanged(self):
        assert format_rich_markdown("Use `pip install` to install") == "Use `pip install` to install"

    def test_blockquotes_unchanged(self):
        quote = "> This is a quote"
        assert format_rich_markdown(quote) == quote

    def test_headers_unchanged(self):
        assert format_rich_markdown("## Section Title") == "## Section Title"

    def test_links_unchanged(self):
        link = "[text](https://example.com)"
        assert format_rich_markdown(link) == link

    def test_spoiler_unchanged(self):
        assert format_rich_markdown("||secret||") == "||secret||"

    def test_strikethrough_unchanged(self):
        assert format_rich_markdown("~~deleted~~") == "~~deleted~~"

    def test_backslash_literal(self):
        """A literal backslash (double-escaped in MDv2) is preserved."""
        assert format_rich_markdown("path\\\\file") == "path\\file"

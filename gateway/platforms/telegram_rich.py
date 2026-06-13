"""Rich Markdown converter for Telegram Rich Messages.

Rich Markdown (used by Telegram's sendRichMessage API) is compatible with
GitHub Flavored Markdown.  The main job of this module is to strip any
MarkdownV2 escape backslashes that might have been applied by the legacy
format_message() path, since Rich Markdown does not need them.

See: https://core.telegram.org/bots/api#rich-message-formatting-options
"""

import re

# Characters that MarkdownV2 requires to be backslash-escaped.
# In Rich Markdown these should appear unescaped.
_MDV2_ESCAPED_RE = re.compile(r'\\([_*\[\]()~`>#+\-=|{}.!\\])')


def format_rich_markdown(content: str) -> str:
    """Convert standard/GFM markdown to Telegram Rich Markdown.

    Rich Markdown is GFM-compatible, so most content passes through
    unchanged.  The only transformation is stripping MarkdownV2 escape
    backslashes (``\\_`` → ``_``, ``\\*`` → ``*``, etc.) that may be
    present if the content was previously processed by format_message().

    Args:
        content: Raw markdown text from the agent.

    Returns:
        Text suitable for the ``markdown`` field of ``InputRichMessage``.
    """
    if not content:
        return content

    return _MDV2_ESCAPED_RE.sub(r'\1', content)

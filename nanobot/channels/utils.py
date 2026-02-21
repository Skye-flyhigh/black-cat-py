"""
Channel utilities - pure functions and constants (no class state).

This module provides:
- Constants: paths, intervals, limits
- Text conversion: markdown_to_telegram_html, extract_markdown_tables
- File helpers: get_file_extension, MIME mappings
- Message formatting: format_reply_context

For the base class and stateful behavior, see base.py.
"""

import re
from pathlib import Path

# ============================================================================
# Constants
# ============================================================================

MEDIA_DIR = Path.home() / ".nanobot" / "media"

# Typing indicator intervals (seconds) - platforms have different timeout behaviors
TYPING_INTERVAL_TELEGRAM = 4  # Telegram typing expires after ~5s
TYPING_INTERVAL_DISCORD = 8  # Discord typing expires after ~10s

# Reconnect delays
RECONNECT_DELAY_SECONDS = 5

# Attachment limits
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB

# Platform message length limits
MAX_MESSAGE_LENGTH_TELEGRAM = 4096
MAX_MESSAGE_LENGTH_DISCORD = 2000


# ============================================================================
# Markdown Conversion
# ============================================================================


def markdown_to_telegram_html(text: str) -> str:
    """
    Convert markdown to Telegram-safe HTML.

    Handles: code blocks, inline code, headers, blockquotes, links,
    bold, italic, strikethrough, and bullet lists.
    """
    if not text:
        return ""

    # 1. Extract and protect code blocks
    code_blocks: list[str] = []

    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", save_code_block, text)

    # 2. Extract and protect inline code
    inline_codes: list[str] = []

    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    # 3. Headers -> plain text
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)

    # 4. Blockquotes -> plain text
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)

    # 5. Escape HTML special characters
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 6. Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # 7. Bold **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 8. Italic _text_ (avoid matching inside words)
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)

    # 9. Strikethrough ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # 10. Bullet lists
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    # 11. Restore inline code
    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore code blocks
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


# Regex to match markdown tables (header + separator + data rows)
_TABLE_RE = re.compile(
    r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
    re.MULTILINE,
)


def parse_markdown_table(table_text: str) -> dict | None:
    """Parse a markdown table into a structured dict (for Feishu cards)."""
    lines = [line.strip() for line in table_text.strip().split("\n") if line.strip()]
    if len(lines) < 3:
        return None

    def split_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    headers = split_row(lines[0])
    rows = [split_row(line) for line in lines[2:]]

    columns = [
        {"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
        for i, h in enumerate(headers)
    ]

    return {
        "tag": "table",
        "page_size": len(rows) + 1,
        "columns": columns,
        "rows": [
            {f"c{i}": row[i] if i < len(row) else "" for i in range(len(headers))} for row in rows
        ],
    }


def extract_markdown_tables(content: str) -> list[dict]:
    """
    Split content into markdown + table elements.

    Returns a list of elements suitable for Feishu cards.
    """
    elements: list[dict] = []
    last_end = 0

    for m in _TABLE_RE.finditer(content):
        before = content[last_end : m.start()].strip()
        if before:
            elements.append({"tag": "markdown", "content": before})

        table = parse_markdown_table(m.group(1))
        if table:
            elements.append(table)
        else:
            elements.append({"tag": "markdown", "content": m.group(1)})

        last_end = m.end()

    remaining = content[last_end:].strip()
    if remaining:
        elements.append({"tag": "markdown", "content": remaining})

    return elements or [{"tag": "markdown", "content": content}]


# ============================================================================
# File Extension Mapping
# ============================================================================

MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "video/mp4": ".mp4",
    "application/pdf": ".pdf",
}

MEDIA_TYPE_TO_EXT = {
    "image": ".jpg",
    "voice": ".ogg",
    "audio": ".mp3",
    "video": ".mp4",
    "file": "",
}


def get_file_extension(media_type: str, mime_type: str | None = None) -> str:
    """Get file extension based on MIME type or media type."""
    if mime_type and mime_type in MIME_TO_EXT:
        return MIME_TO_EXT[mime_type]
    return MEDIA_TYPE_TO_EXT.get(media_type, "")


# ============================================================================
# Reply Context
# ============================================================================


def split_message(text: str, limit: int) -> list[str]:
    """Split a message into chunks that fit within a platform's character limit.

    Tries to split on newlines first, then on spaces, and only hard-splits
    as a last resort.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        # Try to split at the last newline within the limit
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            # Try to split at the last space within the limit
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            # Hard split
            cut = limit

        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")

    return chunks


def format_reply_context(author: str | None, content: str, max_length: int = 200) -> str | None:
    """
    Format a reply/reference message as context.

    Args:
        author: Username or identifier of the original author.
        content: Content of the referenced message.
        max_length: Maximum content length before truncation.

    Returns:
        Formatted string like "[replying to author: content]" or None if no content.
    """
    if not content:
        return None

    content = content.strip()
    if not content:
        return None

    # Truncate long messages
    if len(content) > max_length:
        content = content[:max_length].rsplit(" ", 1)[0] + "..."

    author = author or "someone"
    return f"[replying to {author}: {content}]"

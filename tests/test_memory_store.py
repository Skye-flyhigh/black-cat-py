"""Tests for MemoryStore (daily notes + long-term memory)."""

from datetime import datetime

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.utils.helpers import today_date


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(workspace=tmp_path)


# ── Daily notes ────────────────────────────────────────────────────


def test_read_today_empty(memory):
    assert memory.read_today() == ""


def test_append_and_read_today(memory):
    memory.append_today("fact one")
    content = memory.read_today()
    assert "fact one" in content
    # First entry should have a header
    assert today_date() in content


def test_append_today_multiple(memory):
    memory.append_today("first")
    memory.append_today("second")
    content = memory.read_today()
    assert "first" in content
    assert "second" in content


def test_today_file_path(memory):
    path = memory.get_today_file()
    assert path.name == f"{today_date()}.md"
    assert path.parent == memory.memory_dir


# ── Long-term memory ──────────────────────────────────────────────


def test_read_long_term_empty(memory):
    assert memory.read_long_term() == ""


def test_write_and_read_long_term(memory):
    memory.write_long_term("important fact")
    assert memory.read_long_term() == "important fact"


def test_write_long_term_overwrites(memory):
    memory.write_long_term("old")
    memory.write_long_term("new")
    assert memory.read_long_term() == "new"


# ── Recent memories ───────────────────────────────────────────────


def test_get_recent_memories_empty(memory):
    assert memory.get_recent_memories(days=3) == ""


def test_get_recent_memories_with_today(memory):
    memory.append_today("today's note")
    result = memory.get_recent_memories(days=1)
    assert "today's note" in result


def test_get_recent_memories_separator(memory):
    """Multiple days should be joined by separators."""
    from datetime import timedelta

    today = datetime.now().date()

    # Write notes for today and yesterday
    memory.append_today("today")
    yesterday = today - timedelta(days=1)
    yesterday_file = memory.memory_dir / f"{yesterday.strftime('%Y-%m-%d')}.md"
    yesterday_file.write_text("# yesterday\nyesterday note", encoding="utf-8")

    result = memory.get_recent_memories(days=2)
    assert "today" in result
    assert "yesterday note" in result
    assert "---" in result  # separator


# ── File listing ───────────────────────────────────────────────────


def test_list_memory_files_empty(memory):
    assert memory.list_memory_files() == []


def test_list_memory_files_sorted(memory):
    # Create some dated files
    (memory.memory_dir / "2024-01-01.md").touch()
    (memory.memory_dir / "2024-01-03.md").touch()
    (memory.memory_dir / "2024-01-02.md").touch()

    files = memory.list_memory_files()
    assert len(files) == 3
    # Newest first
    assert files[0].name == "2024-01-03.md"
    assert files[-1].name == "2024-01-01.md"


def test_list_memory_files_ignores_non_date(memory):
    (memory.memory_dir / "MEMORY.md").touch()
    (memory.memory_dir / "notes.md").touch()
    (memory.memory_dir / "2024-01-01.md").touch()

    files = memory.list_memory_files()
    assert len(files) == 1  # Only the date-formatted file


# ── Memory context ─────────────────────────────────────────────────


def test_get_memory_context_empty(memory):
    assert memory.get_memory_context() == ""


def test_get_memory_context_long_term_only(memory):
    memory.write_long_term("I like coffee")
    ctx = memory.get_memory_context()
    assert "Long-term Memory" in ctx
    assert "I like coffee" in ctx


def test_get_memory_context_today_only(memory):
    memory.append_today("meeting at 3pm")
    ctx = memory.get_memory_context()
    assert "Today's Notes" in ctx
    assert "meeting at 3pm" in ctx


def test_get_memory_context_both(memory):
    memory.write_long_term("core fact")
    memory.append_today("today note")
    ctx = memory.get_memory_context()
    assert "Long-term Memory" in ctx
    assert "Today's Notes" in ctx

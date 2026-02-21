"""Tests for filesystem tools (read, write, edit, list_dir)."""

from pathlib import Path

import pytest

from nanobot.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    _resolve_path,
)


# ── _resolve_path ──────────────────────────────────────────────────


def test_resolve_path_absolute():
    p = _resolve_path("/tmp/test.txt")
    # macOS resolves /tmp → /private/tmp, so compare resolved paths
    assert p == Path("/tmp/test.txt").resolve()


def test_resolve_path_relative_with_workspace(tmp_path):
    p = _resolve_path("notes.md", workspace=tmp_path)
    assert p == (tmp_path / "notes.md").resolve()


def test_resolve_path_relative_without_workspace():
    """Without workspace, relative paths resolve against cwd."""
    p = _resolve_path("file.txt")
    assert p.is_absolute()


def test_resolve_path_enforces_allowed_dir(tmp_path):
    allowed = tmp_path / "safe"
    allowed.mkdir()
    with pytest.raises(PermissionError, match="outside allowed directory"):
        _resolve_path("/etc/passwd", allowed_dir=allowed)


def test_resolve_path_allows_within_dir(tmp_path):
    allowed = tmp_path / "safe"
    allowed.mkdir()
    target = allowed / "file.txt"
    target.touch()
    p = _resolve_path(str(target), allowed_dir=allowed)
    assert str(p).startswith(str(allowed.resolve()))


def test_resolve_path_tilde_expansion():
    p = _resolve_path("~/test.txt")
    assert "~" not in str(p)
    assert p.is_absolute()


# ── ReadFileTool ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world", encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path)
    result = await tool.execute(path=str(f))
    assert result == "hello world"


@pytest.mark.asyncio
async def test_read_file_relative_path(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("relative content", encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path)
    result = await tool.execute(path="data.txt")
    assert result == "relative content"


@pytest.mark.asyncio
async def test_read_file_not_found(tmp_path):
    tool = ReadFileTool(workspace=tmp_path)
    result = await tool.execute(path="nonexistent.txt")
    assert "Error" in result
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_read_file_directory(tmp_path):
    tool = ReadFileTool(workspace=tmp_path)
    result = await tool.execute(path=str(tmp_path))
    assert "Error" in result
    assert "Not a file" in result


@pytest.mark.asyncio
async def test_read_file_denied_outside_allowed(tmp_path):
    allowed = tmp_path / "safe"
    allowed.mkdir()
    tool = ReadFileTool(workspace=tmp_path, allowed_dir=allowed)
    result = await tool.execute(path="/etc/hosts")
    assert "Error" in result


# ── WriteFileTool ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_file(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)
    result = await tool.execute(path="output.txt", content="written")
    assert "Successfully wrote" in result
    assert (tmp_path / "output.txt").read_text() == "written"


@pytest.mark.asyncio
async def test_write_file_creates_parents(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)
    result = await tool.execute(path="a/b/c/deep.txt", content="deep")
    assert "Successfully" in result
    assert (tmp_path / "a/b/c/deep.txt").read_text() == "deep"


@pytest.mark.asyncio
async def test_write_file_denied_outside_allowed(tmp_path):
    allowed = tmp_path / "safe"
    allowed.mkdir()
    tool = WriteFileTool(workspace=tmp_path, allowed_dir=allowed)
    result = await tool.execute(path="/tmp/escape.txt", content="nope")
    assert "Error" in result


# ── EditFileTool ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_file_replace(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    tool = EditFileTool(workspace=tmp_path)
    result = await tool.execute(path=str(f), old_text="x = 1", new_text="x = 42")
    assert "Successfully edited" in result
    assert "x = 42" in f.read_text()


@pytest.mark.asyncio
async def test_edit_file_not_found(tmp_path):
    tool = EditFileTool(workspace=tmp_path)
    result = await tool.execute(path="ghost.py", old_text="a", new_text="b")
    assert "Error" in result
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_edit_file_text_not_in_file(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("hello world\n", encoding="utf-8")
    tool = EditFileTool(workspace=tmp_path)
    result = await tool.execute(path=str(f), old_text="goodbye", new_text="farewell")
    assert "Error" in result
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_edit_file_ambiguous_text(tmp_path):
    f = tmp_path / "dup.py"
    f.write_text("x = 1\nx = 1\n", encoding="utf-8")
    tool = EditFileTool(workspace=tmp_path)
    result = await tool.execute(path=str(f), old_text="x = 1", new_text="x = 2")
    assert "Warning" in result
    assert "2 times" in result


@pytest.mark.asyncio
async def test_edit_file_diff_hints(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def hello():\n    print('hi')\n", encoding="utf-8")
    tool = EditFileTool(workspace=tmp_path)
    # Slightly wrong text - should get diff hint
    result = await tool.execute(
        path=str(f), old_text="def hello():\n    print('hello')\n", new_text="replaced"
    )
    assert "similar" in result.lower()


# ── EditFileTool._not_found_message ────────────────────────────────


def test_not_found_message_with_similar_text():
    content = "def hello():\n    print('hi')\n    return True\n"
    old_text = "def hello():\n    print('hello')\n    return True\n"
    msg = EditFileTool._not_found_message(old_text, content, "test.py")
    assert "similar" in msg.lower()
    assert "test.py" in msg


def test_not_found_message_with_no_match():
    content = "completely different content\n"
    old_text = "nothing like this at all\nwith many lines\n"
    msg = EditFileTool._not_found_message(old_text, content, "test.py")
    assert "No similar text found" in msg


# ── ListDirTool ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_dir(tmp_path):
    (tmp_path / "file.txt").touch()
    (tmp_path / "subdir").mkdir()
    tool = ListDirTool(workspace=tmp_path)
    result = await tool.execute(path=str(tmp_path))
    assert "file.txt" in result
    assert "subdir" in result


@pytest.mark.asyncio
async def test_list_dir_empty(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    tool = ListDirTool(workspace=tmp_path)
    result = await tool.execute(path=str(empty))
    assert "empty" in result.lower()


@pytest.mark.asyncio
async def test_list_dir_not_found(tmp_path):
    tool = ListDirTool(workspace=tmp_path)
    result = await tool.execute(path=str(tmp_path / "nope"))
    assert "Error" in result


@pytest.mark.asyncio
async def test_list_dir_on_file(tmp_path):
    f = tmp_path / "file.txt"
    f.touch()
    tool = ListDirTool(workspace=tmp_path)
    result = await tool.execute(path=str(f))
    assert "Error" in result
    assert "Not a directory" in result

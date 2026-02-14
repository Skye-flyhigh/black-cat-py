"""File system tools: read, write, edit."""

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


def _resolve_path(path: str, allowed_dir: Path | None = None) -> Path:
    """Resolve path and optionally enforce directory restriction."""
    resolved = Path(path).expanduser().resolve()
    if allowed_dir and not str(resolved).startswith(str(allowed_dir.resolve())):
        raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


class ReadFileTool(Tool):
    """Tool to read file contents."""

    name = "read_file"
    description = "Read the contents of a file at the given path."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "The file path to read"}},
        "required": ["path"],
    }

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    async def execute(self, **kwargs: Any) -> str:
        path: str = kwargs["path"]
        try:
            file_path = _resolve_path(path, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"

            content = file_path.read_text(encoding="utf-8")
            return content
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {str(e)}"


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    name = "write_file"
    description = "Write content to a file at the given path. Creates parent directories if needed."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The file path to write to"},
            "content": {"type": "string", "description": "The content to write"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    async def execute(self, **kwargs: Any) -> str:
        path: str = kwargs["path"]
        content: str = kwargs["content"]
        try:
            file_path = _resolve_path(path, self._allowed_dir)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    name = "edit_file"
    description = "Edit a file by replacing old_text with new_text. The old_text must exist exactly in the file."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The file path to edit"},
            "old_text": {"type": "string", "description": "The exact text to find and replace"},
            "new_text": {"type": "string", "description": "The text to replace with"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    async def execute(self, **kwargs: Any) -> str:
        path: str = kwargs["path"]
        old_text: str = kwargs["old_text"]
        new_text: str = kwargs["new_text"]
        try:
            file_path = _resolve_path(path, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"

            content = file_path.read_text(encoding="utf-8")

            if old_text not in content:
                return "Error: old_text not found in file. Make sure it matches exactly."

            # Count occurrences
            count = content.count(old_text)
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(old_text, new_text, 1)
            file_path.write_text(new_content, encoding="utf-8")

            return f"Successfully edited {path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {str(e)}"


class ListDirTool(Tool):
    """Tool to list directory contents."""

    name = "list_dir"
    description = "List the contents of a directory."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "The directory path to list"}},
        "required": ["path"],
    }

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    async def execute(self, **kwargs: Any) -> str:
        path: str = kwargs["path"]
        try:
            dir_path = _resolve_path(path, self._allowed_dir)
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"

            items = []
            for item in sorted(dir_path.iterdir()):
                prefix = "📁 " if item.is_dir() else "📄 "
                items.append(f"{prefix}{item.name}")

            if not items:
                return f"Directory {path} is empty"

            return "\n".join(items)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"

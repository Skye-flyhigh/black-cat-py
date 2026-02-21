"""Memory tool for explicit memory operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.memory_manager import Memory


class MemoryTool(Tool):
    """
    Tool for explicit memory operations.

    Actions:
        - remember: Store information with optional tag and categories
        - recall: Search memories by query
        - forget: Delete a memory by ID
    """

    name = "memory"
    description = (
        "Remember, recall, or forget information. "
        "Use 'remember' to store facts, 'recall' to search memories, 'forget' to delete."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["remember", "recall", "forget"],
                "description": "The memory action to perform",
            },
            "content": {
                "type": "string",
                "description": "For remember: the fact to store. For recall: the search query.",
            },
            "tag": {
                "type": "string",
                "enum": ["core", "crucial", "default"],
                "description": "Memory importance (core=permanent, crucial=slow decay, default=normal decay)",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Semantic categories for the memory (e.g., ['preference', 'user'])",
            },
            "memory_id": {
                "type": "string",
                "description": "For forget: the ID of the memory to delete",
            },
        },
        "required": ["action"],
    }

    def __init__(self, memory: "Memory", author: str = "agent"):
        """
        Initialize the memory tool.

        Args:
            memory: The Memory instance for storage operations.
            author: Default author for stored memories.
        """
        self.memory = memory
        self.author = author

    async def execute(self, **kwargs: Any) -> str:
        action: Literal["remember", "recall", "forget"] = kwargs["action"]
        content: str | None = kwargs.get("content")
        tag: Literal["core", "crucial", "default"] = kwargs.get("tag", "default")
        categories: list[str] | None = kwargs.get("categories")
        memory_id: str | None = kwargs.get("memory_id")

        if action == "remember":
            return await self._remember(content, tag, categories)
        elif action == "recall":
            return await self._recall(content)
        elif action == "forget":
            return self._forget(memory_id)
        else:
            return f"Unknown action: {action}"

    async def _remember(
        self,
        content: str | None,
        tag: Literal["core", "crucial", "default"],
        categories: list[str] | None,
    ) -> str:
        """Store a memory."""
        if not content:
            return "Error: content is required for remember action"

        try:
            record = await self.memory.add(
                content=content,
                author=self.author,
                tag=tag,
                categories=categories,
                source="tool",
            )

            if record:
                return (
                    f"Remembered: {content[:100]}{'...' if len(content) > 100 else ''}\n"
                    f"ID: {record.id[:16]}...\n"
                    f"Tag: {tag}, Categories: {categories or []}"
                )
            else:
                return "Memory was deduplicated (already exists or too similar)"
        except Exception as e:
            return f"Error storing memory: {e}"

    async def _recall(self, query: str | None) -> str:
        """Search memories."""
        if not query:
            return "Error: content (query) is required for recall action"

        try:
            results = await self.memory.search(query, limit=5)

            if not results:
                return f"No memories found for: {query}"

            lines = [f"Found {len(results)} memories for: {query}\n"]
            for i, mem in enumerate(results, 1):
                tag = mem.metadata.tag
                weight = f"{mem.metadata.weight:.2f}"
                distance = f"{mem.distance:.3f}" if mem.distance else "?"
                lines.append(
                    f"{i}. [{tag}, w={weight}, d={distance}]\n"
                    f"   {mem.content[:200]}{'...' if len(mem.content) > 200 else ''}\n"
                    f"   ID: {mem.id[:16]}..."
                )

            return "\n".join(lines)
        except Exception as e:
            return f"Error searching memories: {e}"

    def _forget(self, memory_id: str | None) -> str:
        """Delete a memory."""
        if not memory_id:
            return "Error: memory_id is required for forget action"

        try:
            deleted = self.memory.delete(memory_id)
            if deleted:
                return f"Deleted memory: {memory_id}"
            else:
                return f"Memory not found: {memory_id}"
        except Exception as e:
            return f"Error deleting memory: {e}"

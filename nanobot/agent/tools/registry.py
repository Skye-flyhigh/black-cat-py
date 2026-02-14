"""Tool registry for dynamic tool management."""

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """
        Execute a tool by name with given parameters.

        Args:
            name: Tool name.
            params: Tool parameters.

        Returns:
            Tool execution result as string.

        Raises:
            KeyError: If tool not found.
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            return await tool.execute(**params)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def export_md(self, path: Path) -> None:
        """
        Export registered tools to TOOLS.md file for human reading.

        Args:
            path: Path to write TOOLS.md (typically workspace/TOOLS.md)
        """
        lines = [
            "# Available Tools",
            "",
            "This document lists all tools available to nanobot.",
            "Auto-generated from registered tools.",
            "",
        ]

        for tool in self._tools.values():
            lines.append(f"## {tool.name}")
            lines.append("")
            lines.append(tool.description)
            lines.append("")

            # Parameters
            props = tool.parameters.get("properties", {})
            required = set(tool.parameters.get("required", []))

            if props:
                lines.append("**Parameters:**")
                for name, prop in props.items():
                    prop_type = prop.get("type", "any")
                    desc = prop.get("description", "")
                    req = "(required)" if name in required else "(optional)"
                    lines.append(f"- `{name}` ({prop_type}) {req}: {desc}")
                lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")

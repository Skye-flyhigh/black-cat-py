"""Skill management tools: list, get, create, update skills."""

import re
from pathlib import Path
from typing import Any

from blackcat.agent.skills import SkillsLoader
from blackcat.agent.tools.base import Tool, tool_parameters
from blackcat.agent.tools.schema import BooleanSchema, StringSchema, tool_parameters_schema


class _SkillTool(Tool):
    """Shared base for skill tools — common init and path resolution."""

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._skills = SkillsLoader(workspace)

    def _skill_path(self, name: str) -> Path:
        """Get path to skill directory."""
        return self._workspace / "skills" / name

    def _skill_file(self, name: str) -> Path:
        """Get path to SKILL.md file."""
        return self._skill_path(name) / "SKILL.md"

    def _validate_name(self, name: str) -> str | None:
        """Validate skill name follows conventions. Returns error message or None."""
        if not name:
            return "Skill name is required"
        if not re.match(r"^[a-z][a-z0-9-]*$", name):
            return "Skill name must start with lowercase letter and contain only lowercase letters, digits, and hyphens"
        if len(name) > 64:
            return "Skill name must be 64 characters or less"
        return None

    def _parse_frontmatter(self, content: str) -> tuple[dict[str, str], str]:
        """Parse YAML frontmatter from SKILL.md content. Returns (metadata, body)."""
        if not content.startswith("---"):
            return {}, content
        
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL)
        if not match:
            return {}, content
        
        frontmatter_text = match.group(1)
        body = match.group(2).strip()
        
        # Simple YAML key: value parsing
        metadata: dict[str, str] = {}
        for line in frontmatter_text.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip("\"'")
        
        return metadata, body

    def _build_frontmatter(self, name: str, description: str, extra: dict[str, str] | None = None) -> str:
        """Build YAML frontmatter for SKILL.md."""
        lines = ["---", f"name: {name}", f"description: {description}"]
        if extra:
            for key, value in extra.items():
                lines.append(f"{key}: {value}")
        lines.append("---")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# skill_list
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        filter_unavailable=BooleanSchema(
            description="If True, filter out skills with unmet requirements (default true)"
        ),
    )
)
class SkillListTool(_SkillTool):
    """List all available skills."""

    parameters: dict[str, Any]  # type: ignore[assignment]
    """Attached by @tool_parameters decorator."""

    @property
    def name(self) -> str:
        return "skill_list"

    @property
    def description(self) -> str:
        return (
            "List all available skills. Returns skill name, description, source (workspace/builtin), "
            "and availability. Use this to discover what skills exist before using skill_get."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, filter_unavailable: bool = True, **_: Any) -> str:
        try:
            skills = self._skills.list_skills(filter_unavailable=filter_unavailable)
            if not skills:
                return "No skills found."

            lines = ["# Available Skills", ""]
            for s in skills:
                name = s["name"]
                source = s.get("source", "unknown")
                desc = self._skills._get_skill_description(name)
                available = self._skills._check_requirements(self._skills._get_skill_meta(name))
                
                status = "✓" if available else "✗"
                lines.append(f"## {name}")
                lines.append(f"- **Source**: {source}")
                lines.append(f"- **Available**: {status}")
                lines.append(f"- **Description**: {desc}")
                lines.append("")

            return "\n".join(lines)
        except Exception as e:
            return f"Error listing skills: {e}"


# ---------------------------------------------------------------------------
# skill_get
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        name=StringSchema("Skill name to retrieve"),
        required=["name"],
    )
)
class SkillGetTool(_SkillTool):
    """Read a skill's SKILL.md content by name."""

    parameters: dict[str, Any]  # type: ignore[assignment]
    """Attached by @tool_parameters decorator."""

    @property
    def name(self) -> str:
        return "skill_get"

    @property
    def description(self) -> str:
        return (
            "Read a skill's SKILL.md content by name. Returns the full skill content "
            "including YAML frontmatter and markdown body. Use skill_list first to "
            "discover available skills."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, name: str | None = None, **kwargs: Any) -> str:
        try:
            if not name:
                return "Error: Skill name is required."
            
            content = self._skills.load_skill(name)
            if not content:
                available = [s["name"] for s in self._skills.list_skills(filter_unavailable=False)]
                return f"Error: Skill '{name}' not found.\nAvailable skills: {', '.join(available)}"
            
            return content
        except Exception as e:
            return f"Error reading skill '{name}': {e}"


# ---------------------------------------------------------------------------
# skill_create
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        name=StringSchema("Skill name (lowercase, hyphens only, max 64 chars)"),
        skill_description=StringSchema("Brief description of what the skill does"),
        content=StringSchema("Markdown content for the skill body"),
        required=["name", "skill_description", "content"],
    )
)
class SkillCreateTool(_SkillTool):
    """Create a new skill with SKILL.md."""

    parameters: dict[str, Any]  # type: ignore[assignment]
    """Attached by @tool_parameters decorator."""

    @property
    def name(self) -> str:
        return "skill_create"

    @property
    def description(self) -> str:
        return (
            "Create a new skill directory with SKILL.md. The skill name must be lowercase "
            "with hyphens only (e.g., 'my-skill'). This creates the skill in the workspace "
            "skills directory. Use skill_list to see existing skills."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        name: str | None = None,
        skill_description: str | None = None,
        content: str | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            if not name:
                return "Error: Skill name is required."
            if not skill_description:
                return "Error: Skill description is required."
            if content is None:
                return "Error: Skill content is required."

            # Validate name
            validation_error = self._validate_name(name)
            if validation_error:
                return f"Error: {validation_error}"

            # Check if skill already exists
            skill_path = self._skill_path(name)
            skill_file = self._skill_file(name)
            if skill_path.exists():
                return f"Error: Skill '{name}' already exists. Use skill_update to modify."

            # Create skill directory
            skill_path.mkdir(parents=True, exist_ok=True)

            # Build SKILL.md content
            frontmatter = self._build_frontmatter(name, skill_description)
            full_content = f"{frontmatter}\n\n{content.strip()}\n"

            # Write SKILL.md
            skill_file.write_text(full_content, encoding="utf-8")

            return f"Successfully created skill '{name}' at {skill_file}"
        except Exception as e:
            return f"Error creating skill '{name}': {e}"


# ---------------------------------------------------------------------------
# skill_update
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        name=StringSchema("Skill name to update"),
        skill_description=StringSchema("New description (if updating)"),
        content=StringSchema("New markdown content for the skill body (if updating)"),
        required=["name"],
    )
)
class SkillUpdateTool(_SkillTool):
    """Update an existing skill's SKILL.md."""

    parameters: dict[str, Any]  # type: ignore[assignment]
    """Attached by @tool_parameters decorator."""

    @property
    def name(self) -> str:
        return "skill_update"

    @property
    def description(self) -> str:
        return (
            "Update an existing skill's SKILL.md. You can update just the description, "
            "just the content, or both. The skill must already exist. Use skill_list to "
            "see available skills."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        name: str | None = None,
        skill_description: str | None = None,
        content: str | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            if not name:
                return "Error: Skill name is required."
            
            if skill_description is None and content is None:
                return "Error: Provide at least one of 'skill_description' or 'content' to update."

            # Check if skill exists
            skill_file = self._skill_file(name)
            if not skill_file.exists():
                available = [s["name"] for s in self._skills.list_skills(filter_unavailable=False)]
                return f"Error: Skill '{name}' not found.\nAvailable skills: {', '.join(available)}"

            # Read existing content
            existing_content = skill_file.read_text(encoding="utf-8")
            metadata, existing_body = self._parse_frontmatter(existing_content)

            # Update fields
            new_description = skill_description if skill_description is not None else metadata.get("description", name)
            new_body = content.strip() if content is not None else existing_body

            # Preserve extra metadata
            extra = {k: v for k, v in metadata.items() if k not in ("name", "description")}

            # Build updated content
            frontmatter = self._build_frontmatter(name, new_description, extra if extra else None)
            full_content = f"{frontmatter}\n\n{new_body}\n"

            # Write updated SKILL.md
            skill_file.write_text(full_content, encoding="utf-8")

            return f"Successfully updated skill '{name}'"
        except Exception as e:
            return f"Error updating skill '{name}': {e}"
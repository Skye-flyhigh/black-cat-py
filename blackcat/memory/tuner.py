"""Skill auto-evolution — patch existing skills based on behavioural insights."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

from blackcat.agent.skills import SkillsLoader
from blackcat.utils.paths import get_workspace_path


class Tuner: # FIXME: dead code
    """Read skills, compare against behavioural insights, propose or apply edits."""

    def __init__(
        self,
        skills_loader: SkillsLoader | None = None,
        min_evidence: int = 3,
    ) -> None:
        self.loader = skills_loader or SkillsLoader(get_workspace_path())
        self.min_evidence = min_evidence

    def _list_skills(self) -> dict[str, Path]: 
        """Return mapping skill_name → SKILL.md path."""
        entries = self.loader.list_skills(filter_unavailable=False)
        return {e["name"]: Path(e["path"]) for e in entries}

    def _read_skill(self, name: str) -> str | None:
        """Return raw markdown for a skill, or None."""
        return self.loader.load_skill(name)

    def _get_skill_metadata(self, name: str) -> dict | None: # FIXME: dead code
        """Return parsed frontmatter metadata."""
        return self.loader.get_skill_metadata(name)

    def propose_edit( # FIXME: dead code
        self,
        skill_name: str,
        insight: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return an edit proposal dict, or None if not applicable."""
        content = self._read_skill(skill_name)
        if content is None:
            return None

        # Ensure we only edit below frontmatter
        body_start = _find_body_start(content)
        body = content[body_start:]
        sections = _extract_sections(body)

        proposal: dict[str, Any] = {
            "skill": skill_name,
            "insight": insight,
            "rationale": "",
            "edit": None,
        }

        if insight.get("type") == "workflow_skill":
            pattern = insight.get("pattern", [])
            rationale = insight.get("rationale", "")
            if "Patterns" in sections:
                proposal["edit"] = {
                    "section": "Patterns",
                    "action": "append",
                    "text": f"- **{pattern[0]} → {pattern[1]}**: {rationale}\n",
                }
            else:
                proposal["edit"] = {
                    "section": None,
                    "action": "append_end",
                    "text": f"\n## Patterns\n\n- **{pattern[0]} → {pattern[1]}**: {rationale}\n",
                }
            proposal["rationale"] = f"Solid workflow pattern observed {insight.get('occurrences', '?')} times"

        elif insight.get("type") == "guard_skill":
            target = insight.get("target_tool", "")
            rationale = insight.get("rationale", "")
            if "Tips" in sections:
                proposal["edit"] = {
                    "section": "Tips",
                    "action": "append",
                    "text": f"- **Pre-validate before {target}**: {rationale}\n",
                }
            else:
                proposal["edit"] = {
                    "section": None,
                    "action": "append_end",
                    "text": f"\n## Tips\n\n- **Pre-validate before {target}**: {rationale}\n",
                }
            proposal["rationale"] = f"Retry-loop friction on {target} suggests a guard"

        else:
            return None

        return proposal

    def apply_edit(self, proposal: dict[str, Any]) -> bool: # FIXME: dead code
        """Apply a proposal to disk. Returns True if written."""
        edit = proposal.get("edit")
        if not edit:
            return False
        skill_name = proposal["skill"]
        path = self._list_skills().get(skill_name)
        if not path:
            return False

        content = path.read_text(encoding="utf-8")
        body_start = _find_body_start(content)
        before = content[:body_start]
        body = content[body_start:]

        action = edit["action"]
        text = edit["text"]

        if action == "append_end":
            new_body = body.rstrip() + "\n" + text
        elif action == "append" and edit.get("section"):
            section = edit["section"]
            new_body = _append_to_section(body, section, text)
        else:
            logger.warning("Unknown edit action {}", action)
            return False

        path.write_text(before + new_body, encoding="utf-8")
        logger.info("Updated skill {} with insight: {}", skill_name, proposal.get("rationale", ""))
        return True


def _find_body_start(markdown: str) -> int:
    """Return the index immediately after the frontmatter closing ---."""
    if not markdown.startswith("---"):
        return 0
    lines = markdown.splitlines()
    idx = 0
    for i, line in enumerate(lines[1:], start=1):
        idx += len(line) + 1  # +1 for newline
        if line.strip() == "---":
            return idx
    return 0


def _extract_sections(markdown: str) -> dict[str, tuple[int, int]]:
    """Return mapping section title → (start_line, end_line)."""
    lines = markdown.splitlines()
    sections: dict[str, tuple[int, int]] = {}
    current_title: str | None = None
    current_start = 0
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if current_title is not None:
                sections[current_title] = (current_start, i)
            current_title = m.group(1).strip()
            current_start = i
    if current_title is not None:
        sections[current_title] = (current_start, len(lines))
    return sections


def _append_to_section(markdown: str, section: str, text: str) -> str:
    """Insert *text* immediately before the next heading or EOF after *section*."""
    lines = markdown.splitlines()
    in_section = False
    insert_idx = len(lines)
    for i, line in enumerate(lines):
        if re.match(rf"^##\s+{re.escape(section)}\s*$", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^##\s+", line):
            insert_idx = i
            break
    lines.insert(insert_idx, text)
    return "\n".join(lines)

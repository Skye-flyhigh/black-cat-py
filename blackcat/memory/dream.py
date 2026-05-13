# ---------------------------------------------------------------------------
# Dream — heavyweight cron-scheduled memory consolidation
# ---------------------------------------------------------------------------


# Single source of truth for the staleness threshold used in _annotate_with_ages
# *and* in the Phase 1 prompt template (passed as `stale_threshold_days`).
# Keep code and prompt aligned — if you bump this, the LLM's instruction string

import datetime
from typing import Any

from loguru import logger

from blackcat.agent.runner import AgentRunner, AgentRunSpec
from blackcat.agent.tools.registry import ToolRegistry
from blackcat.memory.memory import MemoryStore
from blackcat.providers.base import LLMProvider
from blackcat.session.manager import SessionManager
from blackcat.utils.formatting import truncate_text
from blackcat.utils.prompt_templates import render_template

_STALE_THRESHOLD_DAYS = 14

class Dream:
    """Two-phase memory processor: analyze history.jsonl, then edit files via AgentRunner.

    Phase 1 produces an analysis summary (plain LLM call).
    Phase 2 delegates to AgentRunner with read_file / edit_file tools so the
    LLM can make targeted, incremental edits instead of replacing entire files.
    """

    # Caps on prompt-bound inputs so Dream's LLM calls never exceed the model's
    # context window just because a file (or a legacy large history entry) grew
    # unexpectedly. Each file still appears in full via read_file when the agent
    # needs it in Phase 2 — these caps only bound the Phase 1/2 prompt preview.
    _MEMORY_FILE_MAX_CHARS = 32_000
    _SOUL_FILE_MAX_CHARS = 16_000
    _USER_FILE_MAX_CHARS = 16_000
    _HISTORY_ENTRY_PREVIEW_MAX_CHARS = 4_000

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        max_batch_size: int = 20,
        max_iterations: int = 10,
        max_tool_result_chars: int = 16_000,
        annotate_line_ages: bool = True,
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.max_batch_size = max_batch_size
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars
        # Kill switch for the git-blame-based per-line age annotation in Phase 1.
        # Default True keeps the #3212 behavior; set False to feed MEMORY.md raw
        # (e.g. if a specific LLM reacts poorly to the `← Nd` suffix).
        self.annotate_line_ages = annotate_line_ages
        self._runner = AgentRunner(provider)
        self._tools = self._build_tools()
        self._session_manager = SessionManager(
            workspace=self.store.workspace,
        )
        
    def set_provider(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model
        self._runner.provider = provider

    # -- tool registry -------------------------------------------------------

    def _build_tools(self) -> ToolRegistry:
        """Build a minimal tool registry for the Dream agent."""
        from blackcat.agent.skills import BUILTIN_SKILLS_DIR
        from blackcat.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
        from blackcat.agent.tools.skills import (
            SkillCreateTool,
            SkillGetReferenceTool,
            SkillListReferencesTool,
            SkillListTool,
        )

        tools = ToolRegistry()
        workspace = self.store.workspace
        # Allow reading builtin skills for reference during skill creation
        extra_read = [BUILTIN_SKILLS_DIR] if BUILTIN_SKILLS_DIR.exists() else None
        tools.register(ReadFileTool(
            workspace=workspace,
            allowed_dir=workspace,
            extra_allowed_dirs=extra_read,
        ))
        tools.register(EditFileTool(workspace=workspace, allowed_dir=workspace))
        # write_file resolves relative paths from workspace root, but can only
        # write under skills/ so the prompt can safely use skills/<name>/SKILL.md.
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        tools.register(WriteFileTool(workspace=workspace, allowed_dir=skills_dir))

        tools.register(SkillListTool(workspace=workspace))
        tools.register(SkillCreateTool(workspace=workspace))
        tools.register(SkillListReferencesTool(workspace=workspace))
        tools.register(SkillGetReferenceTool(workspace=workspace))
        return tools

    # -- skill listing --------------------------------------------------------

    def _list_existing_skills(self) -> list[str]:
        """List existing skills as 'name — description' for dedup context."""
        import re as _re

        from blackcat.agent.skills import BUILTIN_SKILLS_DIR

        _desc_re = _re.compile(r"^description:\s*(.+)$", _re.MULTILINE | _re.IGNORECASE)
        entries: dict[str, str] = {}
        for base in (self.store.workspace / "skills", BUILTIN_SKILLS_DIR):
            if not base.exists():
                continue
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                skill_md = d / "SKILL.md"
                if not skill_md.exists():
                    continue
                # Prefer workspace skills over builtin (same name)
                if d.name in entries and base == BUILTIN_SKILLS_DIR:
                    continue
                content = skill_md.read_text(encoding="utf-8")[:500]
                m = _desc_re.search(content)
                desc = m.group(1).strip() if m else "(no description)"
                entries[d.name] = desc
        return [f"{name} — {desc}" for name, desc in sorted(entries.items())]

    def _get_users_files(self) -> dict[str, str]: # TODO: TASK: #53
        """Get the docs of specific users blackcat has interacted with"""
        entries: dict[str, str] = {}
        user_files = list(sorted((self.store.workspace / "users").glob("*.md")))
        for user in user_files:
            if not user.exists():
                continue
            entries[user.name] = truncate_text(self.store.read_file(user), self._USER_FILE_MAX_CHARS)
        return entries

    # -- session logging ------------------------------------------------------

    def _log_session(self, phase: str, data: dict[str, Any]) -> None:
        """Append a structured entry to the Dream session log."""
        import json

        log_dir = self.store.workspace / "logs" / "dream-sessions"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{datetime.datetime.now():%Y-%m-%d}.jsonl"
        entry = {
            "ts": datetime.datetime.now().isoformat(),
            "phase": phase,
            "data": data,
        }
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- session telemetry ---------------------------------------------------

    def _summarise_session(self) -> str:
        """Parse the current session's JSONL and return a one-paragraph telemetry summary, tool calls and errors."""
        import json

        try:
            session_key = self._session_manager.get_session_context()
            # session_context looks like "Channel: discord\nChat ID: 1499311065706791033\n..."
            # Extract channel:chat_id to build the filename
            channel = chat_id = None
            for line in session_key.split("\n"):
                if line.startswith("Channel:"):
                    channel = line.split(":", 1)[1].strip()
                elif line.startswith("Chat ID:"):
                    chat_id = line.split(":", 1)[1].strip()
            if not channel or not chat_id:
                return ""

            safe_key = self._session_manager.safe_key(f"{channel}:{chat_id}")
            session_path = self._session_manager.sessions_dir / f"{safe_key}.jsonl"
            if not session_path.exists():
                return ""

            tool_count = 0
            error_count = 0
            iterations: set[int] = set()
            models: set[str] = set()
            tool_names: list[str] = []
            errors_detail: list[str] = []

            with open(session_path) as f:
                metadata_line = f.readline()
                metadata = json.loads(metadata_line)
                # Model lives in the runtime checkpoint, not per-message
                checkpoint = metadata.get("metadata", {}).get("runtime_checkpoint", {})
                if "model" in checkpoint:
                    models.add(checkpoint["model"])

                for line in f:
                    obj = json.loads(line)
                    if "tool_calls" in obj:
                        for tc in obj["tool_calls"]:
                            tool_count += 1
                            tool_names.append(tc.get("function", {}).get("name", "?"))
                    if obj.get("role") == "tool":
                        content = obj.get("content", "")
                        if isinstance(content, str) and content.startswith("Error:"):
                            error_count += 1
                            errors_detail.append(
                                f"{obj.get('name', '?')}: {content.split(chr(10))[0][:100]}"
                            )
                    if "iteration" in obj:
                        iterations.add(obj["iteration"])

            if tool_count == 0:
                return ""

            model_str = ", ".join(sorted(models)) if models else "unknown"
            iter_str = str(len(iterations)) if iterations else "?"
            unique_tools = sorted(set(tool_names))

            summary = (
                f"## Session Telemetry\n"
                f"- Tool calls: {tool_count}\n"
                f"- Errors: {error_count}\n"
                f"- Iterations: {iter_str} (model: {model_str})\n"
                f"- Tools used: {', '.join(unique_tools)}"
            )
            if errors_detail:
                summary += "\n- Error details:\n"
                for e in errors_detail[:5]:
                    summary += f"  - {e}\n"

            return summary
        except Exception:
            logger.debug("Failed to summarise session telemetry", exc_info=True)
            return ""

    # -- main entry ----------------------------------------------------------

    def _annotate_with_ages(self, content: str) -> str:
        """Append per-line age suffixes to MEMORY.md content.

        Each non-blank line whose age exceeds ``_STALE_THRESHOLD_DAYS`` gets a
        suffix like ``← 30d`` indicating days since last modification.
        Returns the original content unchanged if git is unavailable,
        annotate fails, or the line count doesn't match the age count
        (which can happen with an uncommitted working-tree edit — better to
        skip annotation than to tag the wrong line).
        SOUL.md and USER.md are never annotated.
        """
        file_path = "memory/MEMORY.md"
        try:
            ages = self.store.git.line_ages(file_path)
        except Exception:
            logger.debug("line_ages failed for {}", file_path)
            return content
        if not ages:
            return content

        had_trailing = content.endswith("\n")
        lines = content.splitlines()
        # If HEAD-blob line count disagrees with the working-tree content we
        # received, ages would be assigned to the wrong lines — skip entirely
        # and feed the LLM un-annotated content rather than misleading data.
        if len(lines) != len(ages):
            logger.debug(
                "line_ages length mismatch for {} (lines={}, ages={}); skipping annotation",
                file_path, len(lines), len(ages),
            )
            return content

        annotated: list[str] = []
        for line, age in zip(lines, ages):
            if not line.strip():
                annotated.append(line)
                continue
            if age.age_days > _STALE_THRESHOLD_DAYS:
                annotated.append(f"{line}  \u2190 {age.age_days}d")
            else:
                annotated.append(line)
        result = "\n".join(annotated)
        if had_trailing:
            result += "\n"
        return result

    # -- public API -----------------------------------------------------------

    async def run(self) -> bool:
        """Process unprocessed history entries. Returns True if work was done."""
        from blackcat.agent.skills import BUILTIN_SKILLS_DIR

        last_cursor = self.store.get_last_dream_cursor()
        entries = self.store.read_unprocessed_history(since_cursor=last_cursor)
        if not entries:
            return False

        batch = entries[: self.max_batch_size]
        logger.info(
            "Dream: processing {} entries (cursor {}→{}), batch={}",
            len(entries), last_cursor, batch[-1]["cursor"], len(batch),
        )

        self._log_session(
            "start",
            {
                "batch_size": len(batch),
                "cursor_from": last_cursor,
                "cursor_to": batch[-1]["cursor"],
                "total_entries": len(entries),
            },
        )

        # Build history text for LLM — cap each entry so a legacy oversized
        # record (e.g. pre-#3412 raw_archive dump) can't blow up the prompt.
        history_text = "\n".join(
            f"[{e['timestamp']}] "
            f"{truncate_text(e['content'], self._HISTORY_ENTRY_PREVIEW_MAX_CHARS)}"
            for e in batch
        )

        # Current file contents + per-line age annotations (MEMORY.md only).
        # Each file is capped in the *prompt preview* only; Phase 2 still sees
        # the full file via the read_file tool.
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        raw_memory = self.store.read_memory() or "(empty)"
        annotated_memory = (
            self._annotate_with_ages(raw_memory)
            if self.annotate_line_ages
            else raw_memory
        )
        current_memory = truncate_text(annotated_memory, self._MEMORY_FILE_MAX_CHARS)
        current_soul = truncate_text(
            self.store.read_soul() or "(empty)", self._SOUL_FILE_MAX_CHARS,
        )
        current_user = truncate_text(
            self.store.read_user() or "(empty)", self._USER_FILE_MAX_CHARS,
        )

        session_context = self._session_manager.get_session_context()
        session_telemetry = self._summarise_session()

        file_context = (
            f"## Current Date\n{current_date}\n\n"
            f"## Current MEMORY.md ({len(current_memory)} chars)\n{current_memory}\n\n"
            f"## Current SOUL.md ({len(current_soul)} chars)\n{current_soul}\n\n"
            f"## Current USER.md ({len(current_user)} chars)\n{current_user}\n\n"
            f"## Session Context\n{session_context}"
        )

        # Phase 1: Analyze (no skills list — dedup is Phase 2's job)
        phase1_prompt = (
            f"## Conversation History\n{history_text}\n\n"
            f"{file_context}"
        )

        try:
            phase1_response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template(
                            "agent/dream_phase1.md",
                            strip=True,
                            stale_threshold_days=_STALE_THRESHOLD_DAYS,
                            telemetry=session_telemetry or "(no telemetry available)",
                        ),
                    },
                    {"role": "user", "content": phase1_prompt},
                ],
                tools=None,
                tool_choice=None,
            )
            analysis = phase1_response.content or ""
            self._log_session(
                "phase1_complete",
                {
                    "analysis_chars": len(analysis),
                    "analysis": analysis
                },
            )
            logger.debug("Dream Phase 1 analysis ({} chars): {}", len(analysis), analysis[:500])
        except Exception:
            self._log_session("phase1_failed", {
                "reason": phase1_response.finish_reason or "Undefined",
                "error": phase1_response.error_kind + phase1_response.error_type
            })
            logger.exception("Dream Phase 1 failed")
            return False

        # Phase 2: Delegate to AgentRunner with read_file / edit_file
        existing_skills = self._list_existing_skills()
        skills_section = ""
        if existing_skills:
            skills_section = (
                "\n\n## Existing Skills\n"
                + "\n".join(f"- {s}" for s in existing_skills)
            )
        phase2_prompt = f"## Analysis Result\n{analysis}\n\n{file_context}{skills_section}"

        tools = self._tools
        skill_creator_path = BUILTIN_SKILLS_DIR / "skill-creator" / "SKILL.md"
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": render_template(
                    "agent/dream_phase2.md",
                    strip=True,
                    skill_creator_path=str(skill_creator_path),
                ),
            },
            {"role": "user", "content": phase2_prompt},
        ]

        try:
            result = await self._runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=self.max_iterations,
                max_tool_result_chars=self.max_tool_result_chars,
                fail_on_tool_error=False,
            ))
            logger.debug(
                "Dream Phase 2 complete: stop_reason={}, tool_events={}",
                result.stop_reason, len(result.tool_events),
            )
            self._log_session(
                "phase2_complete",
                {
                    "content": result.final_content,
                    "stop_reason": result.stop_reason,
                    "tool_events": len(result.tool_events or []),
                },
            )
            for ev in (result.tool_events or []):
                logger.info("Dream tool_event: name={}, status={}, detail={}", ev.get("name"), ev.get("status"), ev.get("detail", "")[:200])
        except Exception:
            self._log_session("phase2_failed", {})
            logger.exception("Dream Phase 2 failed")
            result = None

        # Build changelog from tool events
        changelog: list[str] = []
        if result and result.tool_events:
            for event in result.tool_events:
                if event["status"] == "ok":
                    changelog.append(f"{event['name']}: {event['detail']}")

        # Advance cursor — always, to avoid re-processing Phase 1
        new_cursor = batch[-1]["cursor"]
        reason = result.stop_reason if result else "exception"
        self.store.set_last_dream_cursor(new_cursor)
        self._log_session(
            "done",
            {
                "cursor": new_cursor,
                "changelog_entries": len(changelog),
                "stop_reason": reason,
            },
        )
        self.store.compact_history()

        if result and result.stop_reason == "completed":
            logger.info(
                "Dream done: {} change(s), cursor advanced to {}",
                len(changelog), new_cursor,
            )
        else:
            
            logger.warning(
                "Dream incomplete ({}): cursor advanced to {}",
                reason, new_cursor,
            )

        # Git auto-commit (only when there are actual changes)
        if changelog and self.store.git.is_initialized():
            ts = batch[-1]["timestamp"]
            summary = f"dream: {ts}, {len(changelog)} change(s)"
            commit_msg = f"{summary}\n\n{analysis.strip()}"
            sha = self.store.git.auto_commit(commit_msg)
            if sha:
                logger.info("Dream commit: {}", sha)

        return True

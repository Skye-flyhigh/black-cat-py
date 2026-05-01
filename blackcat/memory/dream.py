"""Dream — thin orchestrator that delegates to blackcat.dream modules.

This module exists for backwards compatibility; new code should import
from ``blackcat.dream`` directly.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from blackcat.agent.skills import SkillsLoader
from blackcat.agent.subagent import SubAgent
from blackcat.config.schema import Config
from blackcat.dream import BehavioralAnalyzer, SessionParser, SkillEvolver
from blackcat.memory.memory import MemoryStore
from blackcat.utils.paths import get_workspace_dir
from blackcat.utils.prompt_templates import render_template


class Dream:
    """Run the dream consolidation pipeline with behavioural telemetry."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config
        self.workspace_dir = get_workspace_dir()
        self.memory_store = MemoryStore(self.workspace_dir)
        self.parser = SessionParser()
        self.analyzer = BehavioralAnalyzer()
        self.evolver = SkillEvolver(skills_loader=SkillsLoader(self.workspace_dir))

    def run(
        self,
        *,
        model: str | None = None,
        max_input_chars: int = 100_000,
        recent_hours: int = 24,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute the full dream pipeline.

        Args:
            model: Override model for sub-agent.
            max_input_chars: Conversation history budget.
            recent_hours: How far back to look for sessions.
            dry_run: If True, skip all writes (preview only).

        Returns:
            Structured report of what dream did.
        """
        report: dict[str, Any] = {
            "started_at": datetime.now().isoformat(),
            "phases": [],
        }

        # --- Phase 1: content consolidation (conversation history) ---
        logger.info("Dream Phase 1 — content consolidation")
        phase1 = self._phase1_content(
            model=model, max_input_chars=max_input_chars, dry_run=dry_run
        )
        report["phases"].append({"phase": 1, "result": phase1})

        # --- Phase 1b: behavioural telemetry (new) ---
        logger.info("Dream Phase 1b — behavioural telemetry")
        since = datetime.now() - timedelta(hours=recent_hours)
        summaries = self.parser.parse_all(since=since)
        analysis = self.analyzer.analyse(summaries)
        report["phases"].append(
            {
                "phase": "1b",
                "sessions": len(summaries),
                "patterns_found": len(analysis["patterns"]),
                "friction_found": len(analysis["friction"]),
                "skill_candidates": len(analysis["skill_candidates"]),
                "details": analysis,
            }
        )

        # Persist behavioural insights to mnemo
        if not dry_run:
            self._persist_behavioral_insights(analysis)

        # --- Phase 2: skill evolution (new) ---
        logger.info("Dream Phase 2 — skill evolution")
        phase2 = self._phase2_skills(
            analysis=analysis, model=model, dry_run=dry_run
        )
        report["phases"].append({"phase": 2, "result": phase2})

        report["finished_at"] = datetime.now().isoformat()
        return report

    # ------------------------------------------------------------------
    # Phase 1 — conversation history (unchanged logic, moved here)
    # ------------------------------------------------------------------

    def _phase1_content(
        self,
        *,
        model: str | None,
        max_input_chars: int,
        dry_run: bool,
    ) -> dict[str, Any]:
        history_path = self.workspace_dir / "memory" / "history.jsonl"
        if not history_path.exists():
            return {"status": "skipped", "reason": "no history"}

        history_lines = self._read_history(history_path, max_input_chars)
        if not history_lines:
            return {"status": "skipped", "reason": "empty history"}

        # Inject behavioural digest into Phase-1 prompt
        since = datetime.now() - timedelta(hours=24)
        digest = self.parser.recent_behavioral_digest(since=since, max_sessions=5)

        prompt = render_template(
            "agent/dream_phase1.md",
            history=history_lines,
            behavioral_digest=json.dumps(digest, indent=2, default=str),
        )

        sub = SubAgent(
            system_prompt=render_template("agent/dream_system.md"),
            model=model,
            max_iterations=1,
        )
        result = sub.run(prompt)

        phase1_result: dict[str, Any] = {
            "status": "completed",
            "output_length": len(result),
        }

        if not dry_run:
            memory_path = self.workspace_dir / "memory" / "MEMORY.md"
            self._write_memory(memory_path, result)
            phase1_result["memory_updated"] = True

        return phase1_result

    def _read_history(self, path: Path, max_chars: int) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to read history")
            return ""
        if len(text) > max_chars:
            text = text[-max_chars:]
            # Try to start at a line boundary
            if "\n" in text:
                text = text[text.index("\n") + 1 :]
        return text

    def _write_memory(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(content, encoding="utf-8")
            logger.info("Dream updated {}", path)
        except Exception:
            logger.exception("Failed to write memory")

    # ------------------------------------------------------------------
    # Phase 1b helpers
    # ------------------------------------------------------------------

    def _persist_behavioral_insights(self, analysis: dict[str, Any]) -> None:
        for pattern in analysis.get("patterns", []):
            self.memory_store.remember(
                content=json.dumps(pattern, default=str),
                namespace="behavior",
                tag="default",
                categories=["behavior-pattern"],
            )
        for friction in analysis.get("friction", []):
            self.memory_store.remember(
                content=json.dumps(friction, default=str),
                namespace="behavior",
                tag="default",
                categories=["friction-point"],
            )
        for candidate in analysis.get("skill_candidates", []):
            self.memory_store.remember(
                content=json.dumps(candidate, default=str),
                namespace="behavior",
                tag="crucial",
                categories=["skill-candidate"],
            )
        logger.info(
            "Persisted {} patterns, {} friction, {} candidates to mnemo",
            len(analysis.get("patterns", [])),
            len(analysis.get("friction", [])),
            len(analysis.get("skill_candidates", [])),
        )

    # ------------------------------------------------------------------
    # Phase 2 — skill evolution
    # ------------------------------------------------------------------

    def _phase2_skills(
        self,
        *,
        analysis: dict[str, Any],
        model: str | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        candidates = analysis.get("skill_candidates", [])
        if not candidates:
            return {"status": "skipped", "reason": "no candidates"}

        # Filter: only high-confidence candidates with existing skill target
        skills = self.evolver.list_skills()
        actionable: list[dict[str, Any]] = []
        for c in candidates:
            # Map candidate to a skill name heuristically
            target = _guess_skill_target(c, set(skills.keys()))
            if target:
                c["_target_skill"] = target
                actionable.append(c)

        if not actionable:
            return {"status": "skipped", "reason": "no matching skills"}

        # Build Phase-2 prompt with candidate list
        prompt = render_template(
            "agent/dream_phase2.md",
            candidates=json.dumps(actionable, indent=2, default=str),
            skills_dir=str(self.evolver.skills_dir),
        )

        sub = SubAgent(
            system_prompt=render_template("agent/dream_system.md"),
            model=model,
            max_iterations=5,
        )
        result = sub.run(prompt)

        phase2_result: dict[str, Any] = {
            "status": "completed",
            "candidates": len(actionable),
            "output_length": len(result),
        }

        if not dry_run:
            # The sub-agent may have edited skills via its file tools.
            # We also auto-apply simple proposals ourselves.
            applied = 0
            for c in actionable:
                proposal = self.evolver.propose_edit(c["_target_skill"], c)
                if proposal and self.evolver.apply_edit(proposal):
                    applied += 1
            phase2_result["auto_applied"] = applied

        return phase2_result


def _guess_skill_target(candidate: dict[str, Any], existing: set[str]) -> str | None:
    """Heuristic: which existing skill should receive this insight?"""
    # Direct name match
    for key in ("name", "target_tool"):
        val = candidate.get(key)
        if val and val in existing:
            return val
    # Substring match
    for key in ("name", "target_tool"):
        val = candidate.get(key, "")
        for skill in existing:
            if val.lower() in skill.lower() or skill.lower() in val.lower():
                return skill
    return None

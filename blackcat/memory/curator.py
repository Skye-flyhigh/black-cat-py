"""Dream behavioural analysis — detect patterns from parsed session telemetry."""

from __future__ import annotations

from collections import Counter
from typing import Any

from loguru import logger

from blackcat.session.parser import SessionSummary


class Curator: # FIXME: dead code
    """Analyse session summaries for recurring patterns."""

    def __init__(self, min_occurrences: int = 3) -> None:
        self.min_occurrences = min_occurrences

    def analyse(self, summaries: list[SessionSummary]) -> dict[str, Any]: # FIXME: dead code
        """Return a structured report of patterns, friction, and skill candidates."""
        if not summaries:
            return {"patterns": [], "friction": [], "skill_candidates": []}

        all_sequences: list[list[str]] = [s.tool_sequence for s in summaries]
        all_errors: list[str] = []
        all_loops: list[dict[str, Any]] = []

        for s in summaries:
            for r in s.retry_loops:
                all_loops.append(r)
            # Collect error hints
            for t in s.turns:
                for res in t.tool_results:
                    if not res.success and res.error_hint:
                        all_errors.append(res.error_hint)

        patterns = self._find_success_patterns(all_sequences)
        friction = self._find_friction(all_loops, all_errors, all_sequences)
        candidates = self._suggest_skills(patterns, friction)

        return {
            "patterns": patterns,
            "friction": friction,
            "skill_candidates": candidates,
        }

    def _find_success_patterns(self, sequences: list[list[str]]) -> list[dict[str, Any]]:
        """Look for common 2-tool sequences that appear frequently."""
        pair_counts: Counter[tuple[str, str]] = Counter()
        for seq in sequences:
            for i in range(len(seq) - 1):
                pair_counts[(seq[i], seq[i + 1])] += 1

        patterns: list[dict[str, Any]] = []
        for (a, b), count in pair_counts.most_common(10):
            if count >= self.min_occurrences:
                patterns.append(
                    {
                        "type": "tool_pair",
                        "sequence": [a, b],
                        "occurrences": count,
                        "confidence": "high" if count >= 5 else "medium",
                    }
                )
        return patterns

    def _find_friction(
        self,
        loops: list[dict[str, Any]],
        errors: list[str],
        sequences: list[list[str]],
    ) -> list[dict[str, Any]]:
        """Identify repeated failures and inefficiencies."""
        friction: list[dict[str, Any]] = []

        # Retry loops
        loop_tools = Counter(l["tool"] for l in loops)
        for tool, count in loop_tools.most_common(5):
            if count >= 2:
                friction.append(
                    {
                        "type": "retry_loop",
                        "tool": tool,
                        "occurrences": count,
                        "hint": f"Consider pre-validation before calling {tool}",
                    }
                )

        # Common errors
        error_types = Counter(errors)
        for err, count in error_types.most_common(5):
            if count >= 2:
                friction.append(
                    {
                        "type": "recurring_error",
                        "error": err,
                        "occurrences": count,
                        "hint": "May need a guard or fallback strategy",
                    }
                )

        # Tool over-use (single tool dominating a session)
        flat = [t for seq in sequences for t in seq]
        counts = Counter(flat)
        for tool, cnt in counts.most_common(3):
            if cnt > 10:
                friction.append(
                    {
                        "type": "tool_overuse",
                        "tool": tool,
                        "total_calls": cnt,
                        "hint": f"Consider batching or alternative approach for {tool}",
                    }
                )

        return friction

    def _suggest_skills(
        self,
        patterns: list[dict[str, Any]],
        friction: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Turn solid patterns into skill candidates."""
        candidates: list[dict[str, Any]] = []

        for p in patterns:
            if p.get("confidence") == "high":
                seq = p["sequence"]
                candidates.append(
                    {
                        "type": "workflow_skill",
                        "name": f"{seq[0]}_then_{seq[1]}",
                        "rationale": f"Observed {p['occurrences']} times — solid pattern",
                        "pattern": seq,
                    }
                )

        for f in friction:
            if f["type"] == "retry_loop":
                candidates.append(
                    {
                        "type": "guard_skill",
                        "name": f"pre_validate_{f['tool']}",
                        "rationale": f"Retry loop on {f['tool']} seen {f['occurrences']} times",
                        "target_tool": f["tool"],
                    }
                )

        logger.debug("Generated {} skill candidates", len(candidates))
        return candidates

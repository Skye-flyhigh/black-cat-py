"""Parse session JSONL files into structured behavioral telemetry.

Extracts tool-call sequences, error patterns, retry loops, and iteration
depth from ``sessions/*.jsonl`` for downstream self-improvement analysis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from blackcat.utils.paths import get_workspace_path


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation within a session."""

    tool_name: str
    call_id: str
    arguments: dict[str, Any]
    timestamp: datetime | None = None


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a tool invocation."""

    call_id: str
    success: bool
    error_hint: str | None = None  # e.g. "FileNotFoundError" or truncated message
    timestamp: datetime | None = None


@dataclass
class Turn:
    """One assistant turn (assistant message + its tool calls + their results)."""

    iteration: int
    model: str | None
    reasoning: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    stop_reason: str | None = None  # e.g. "tool_calls" or "stop"
    timestamp: datetime | None = None


@dataclass
class SessionSummary:
    """Behavioral summary of a single session file."""

    session_key: str
    start_time: datetime | None
    end_time: datetime | None
    total_turns: int = 0
    total_tool_calls: int = 0
    error_count: int = 0
    retry_loops: list[dict[str, Any]] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionParser:
    """Parse ``sessions/*.jsonl`` into structured behavioral telemetry."""

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self.sessions_dir = sessions_dir or get_workspace_path() / "sessions"

    def parse_all(self, since: datetime | None = None) -> list[SessionSummary]:
        """Parse every ``*.jsonl`` session file.

        Args:
            since: Only include sessions updated after this time.

        Returns:
            List of session summaries ordered by start time (descending).
        """
        summaries: list[SessionSummary] = []
        if not self.sessions_dir.exists():
            logger.warning("Sessions directory not found: {}", self.sessions_dir)
            return summaries

        for path in sorted(self.sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                summary = self.parse_file(path)
            except Exception:
                logger.exception("Failed to parse session {}", path.name)
                continue
            if since is not None and summary.end_time is not None and summary.end_time < since:
                continue
            summaries.append(summary)

        return summaries

    def parse_file(self, path: Path) -> SessionSummary:
        """Parse a single session JSONL file."""
        session_key: str = ""
        turns: list[Turn] = []
        current_turn: Turn | None = None
        metadata: dict[str, Any] = {}
        start_time: datetime | None = None
        end_time: datetime | None = None

        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                record_type = record.get("_type")
                if record_type == "metadata":
                    session_key = record.get("key", "")
                    meta = record.get("metadata", {})
                    metadata.update(meta)
                    ts = record.get("created_at")
                    if ts:
                        start_time = _parse_iso(ts)
                    ts = record.get("updated_at")
                    if ts:
                        end_time = _parse_iso(ts)
                    continue

                role = record.get("role")
                ts = record.get("timestamp")
                parsed_ts = _parse_iso(ts) if ts else None

                if role == "assistant":
                    # Finalise previous turn
                    if current_turn is not None:
                        turns.append(current_turn)
                    checkpoint = record.get("metadata", {}).get("runtime_checkpoint", {})
                    current_turn = Turn(
                        iteration=checkpoint.get("iteration", 0),
                        model=checkpoint.get("model"),
                        reasoning=record.get("reasoning_content"),
                        stop_reason=record.get("stop_reason"),
                        timestamp=parsed_ts,
                    )
                    # Attach tool calls from this assistant message
                    for tc in record.get("tool_calls") or []:
                        fn = tc.get("function", {})
                        current_turn.tool_calls.append(
                            ToolCall(
                                tool_name=fn.get("name", "unknown"),
                                call_id=tc.get("id", ""),
                                arguments=_safe_load_json(fn.get("arguments", "{}")),
                                timestamp=parsed_ts,
                            )
                        )

                elif role == "tool" and current_turn is not None:
                    content = record.get("content", "")
                    error_hint = None
                    success = not _looks_like_error(content)
                    if not success:
                        error_hint = _extract_error_hint(content)
                        current_turn.tool_results.append(
                            ToolResult(
                                call_id=record.get("tool_call_id", ""),
                                success=False,
                                error_hint=error_hint,
                                timestamp=parsed_ts,
                            )
                        )
                    else:
                        current_turn.tool_results.append(
                            ToolResult(
                                call_id=record.get("tool_call_id", ""),
                                success=True,
                                timestamp=parsed_ts,
                            )
                        )

        if current_turn is not None:
            turns.append(current_turn)

        # Build summary
        total_tool_calls = sum(len(t.tool_calls) for t in turns)
        error_count = sum(
            1 for t in turns for r in t.tool_results if not r.success
        )
        tool_sequence = [tc.tool_name for t in turns for tc in t.tool_calls]

        summary = SessionSummary(
            session_key=session_key,
            start_time=start_time,
            end_time=end_time,
            total_turns=len(turns),
            total_tool_calls=total_tool_calls,
            error_count=error_count,
            retry_loops=_detect_retry_loops(turns),
            tool_sequence=tool_sequence,
            turns=turns,
            metadata=metadata,
        )
        return summary

    def recent_behavioral_digest(
        self,
        since: datetime | None = None,
        max_sessions: int = 5,
    ) -> dict[str, Any]:
        """Return a concise digest for Dream prompt injection.

        Args:
            since: Look-back window (defaults to last 24 h).
            max_sessions: How many recent sessions to include.

        Returns:
            Dict ready for JSON serialisation and prompt insertion.
        """
        if since is None:
            since = datetime.now() - __import__("datetime").timedelta(days=1)

        summaries = self.parse_all(since=since)[:max_sessions]
        return {
            "period": f"{since.isoformat()} → now",
            "sessions_analysed": len(summaries),
            "sessions": [_summarise_for_prompt(s) for s in summaries],
        }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_load_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _looks_like_error(content: Any) -> bool:
    """Heuristic: does a tool result look like a failure?"""
    if not isinstance(content, str):
        return False
    lower = content.lower()
    error_markers = (
        "error:", "exception:", "failed", "traceback", "not found",
        "permission denied", "timeout", "exit code", "does not exist",
    )
    return any(m in lower for m in error_markers)


def _extract_error_hint(content: str, max_len: int = 120) -> str:
    """Grab the first line that smells like an error."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and any(
            stripped.lower().startswith(p)
            for p in ("error", "exception", "traceback", "filenotfound")
        ):
            return stripped[:max_len]
    return content[:max_len]


def _detect_retry_loops(turns: list[Turn]) -> list[dict[str, Any]]:
    """Find consecutive assistant turns that repeat the same tool call."""
    loops: list[dict[str, Any]] = []
    i = 0
    while i < len(turns) - 1:
        t1 = turns[i]
        t2 = turns[i + 1]
        if not t1.tool_calls or not t2.tool_calls:
            i += 1
            continue
        # Same first tool repeated with likely same intent
        if t1.tool_calls[0].tool_name == t2.tool_calls[0].tool_name:
            # Check if previous turn had an error on that tool
            had_error = any(not r.success for r in t1.tool_results)
            loops.append(
                {
                    "tool": t1.tool_calls[0].tool_name,
                    "iterations": [t1.iteration, t2.iteration],
                    "followed_error": had_error,
                }
            )
            i += 2
        else:
            i += 1
    return loops


def _summarise_for_prompt(summary: SessionSummary) -> dict[str, Any]:
    """Flatten a SessionSummary into a Dream-friendly dict."""
    return {
        "key": summary.session_key,
        "turns": summary.total_turns,
        "tools": summary.total_tool_calls,
        "errors": summary.error_count,
        "sequence": summary.tool_sequence,
        "retry_loops": summary.retry_loops,
        "friction_points": _friction_points(summary),
    }


def _friction_points(summary: SessionSummary) -> list[str]:
    """Human-readable friction heuristics."""
    points: list[str] = []
    if summary.error_count > summary.total_tool_calls * 0.3:
        points.append("high error rate")
    if len(summary.retry_loops) > 2:
        points.append("repeated retry loops")
    # Detect tool over-use
    from collections import Counter
    counts = Counter(summary.tool_sequence)
    for tool, cnt in counts.most_common(3):
        if cnt > 5:
            points.append(f"heavy {tool} use ({cnt}x)")
    return points

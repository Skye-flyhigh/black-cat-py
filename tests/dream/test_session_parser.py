"""Tests for blackcat.dream.session_parser."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from blackcat.dream.session_parser import SessionParser, _looks_like_error


class TestLooksLikeError:
    def test_error_markers(self) -> None:
        assert _looks_like_error("Error: file not found")
        assert _looks_like_error("Exception: timeout")
        assert _looks_like_error("Traceback (most recent call)")
        assert _looks_like_error("Failed to connect")
        assert _looks_like_error("Permission denied")

    def test_non_errors(self) -> None:
        assert not _looks_like_error("Here is the result")
        assert not _looks_like_error(42)
        assert not _looks_like_error(None)


class TestSessionParser:
    def test_parse_empty_dir(self, tmp_path: Path) -> None:
        parser = SessionParser(sessions_dir=tmp_path)
        summaries = parser.parse_all()
        assert summaries == []

    def test_parse_single_session(self, tmp_path: Path) -> None:
        session_file = tmp_path / "discord_123.jsonl"
        records = [
            {
                "_type": "metadata",
                "key": "discord:123",
                "created_at": datetime.now().isoformat(),
                "metadata": {"channel": "discord"},
            },
            {
                "role": "user",
                "content": "hello",
                "timestamp": datetime.now().isoformat(),
            },
            {
                "role": "assistant",
                "content": "hi",
                "timestamp": datetime.now().isoformat(),
                "metadata": {
                    "runtime_checkpoint": {"iteration": 1, "model": "gpt-4"}
                },
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/tmp/test"}',
                        },
                    }
                ],
                "stop_reason": "tool_calls",
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "file contents",
                "timestamp": datetime.now().isoformat(),
            },
        ]
        session_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        parser = SessionParser(sessions_dir=tmp_path)
        summaries = parser.parse_all()
        assert len(summaries) == 1
        s = summaries[0]
        assert s.session_key == "discord:123"
        assert s.total_turns == 1
        assert s.total_tool_calls == 1
        assert s.error_count == 0
        assert s.tool_sequence == ["read_file"]

    def test_detects_error(self, tmp_path: Path) -> None:
        session_file = tmp_path / "telegram_456.jsonl"
        records = [
            {
                "_type": "metadata",
                "key": "telegram:456",
                "created_at": datetime.now().isoformat(),
            },
            {
                "role": "assistant",
                "content": "",
                "metadata": {
                    "runtime_checkpoint": {"iteration": 1, "model": "claude"}
                },
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "exec",
                            "arguments": '{"command": "bad"}',
                        },
                    }
                ],
                "stop_reason": "tool_calls",
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "Error: command not found: bad",
            },
        ]
        session_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        parser = SessionParser(sessions_dir=tmp_path)
        summaries = parser.parse_all()
        assert summaries[0].error_count == 1

    def test_retry_loop_detection(self, tmp_path: Path) -> None:
        session_file = tmp_path / "discord_789.jsonl"
        base = datetime.now().isoformat()
        records = [
            {
                "_type": "metadata",
                "key": "discord:789",
                "created_at": base,
            },
            {
                "role": "assistant",
                "content": "",
                "metadata": {
                    "runtime_checkpoint": {"iteration": 1, "model": "gpt-4"}
                },
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
                "stop_reason": "tool_calls",
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "Error: not found",
            },
            {
                "role": "assistant",
                "content": "",
                "metadata": {
                    "runtime_checkpoint": {"iteration": 2, "model": "gpt-4"}
                },
                "tool_calls": [
                    {
                        "id": "c2",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
                "stop_reason": "tool_calls",
            },
            {
                "role": "tool",
                "tool_call_id": "c2",
                "content": "ok",
            },
        ]
        session_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        parser = SessionParser(sessions_dir=tmp_path)
        summaries = parser.parse_all()
        loops = summaries[0].retry_loops
        assert len(loops) == 1
        assert loops[0]["tool"] == "read_file"
        assert loops[0]["followed_error"] is True

    def test_since_filter(self, tmp_path: Path) -> None:
        old = tmp_path / "old.jsonl"
        new = tmp_path / "new.jsonl"
        old.write_text(
            json.dumps(
                {
                    "_type": "metadata",
                    "key": "old",
                    "created_at": (datetime.now() - timedelta(days=2)).isoformat(),
                    "updated_at": (datetime.now() - timedelta(days=2)).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        new.write_text(
            json.dumps(
                {
                    "_type": "metadata",
                    "key": "new",
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
            ),
            encoding="utf-8",
        )

        parser = SessionParser(sessions_dir=tmp_path)
        since = datetime.now() - timedelta(hours=12)
        summaries = parser.parse_all(since=since)
        assert len(summaries) == 1
        assert summaries[0].session_key == "new"

    def test_digest_structure(self, tmp_path: Path) -> None:
        session_file = tmp_path / "discord_999.jsonl"
        records = [
            {
                "_type": "metadata",
                "key": "discord:999",
                "created_at": datetime.now().isoformat(),
            },
            {
                "role": "assistant",
                "content": "",
                "metadata": {
                    "runtime_checkpoint": {"iteration": 1, "model": "gpt-4"}
                },
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
                "stop_reason": "tool_calls",
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "ok",
            },
        ]
        session_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        parser = SessionParser(sessions_dir=tmp_path)
        digest = parser.recent_behavioral_digest(max_sessions=5)
        assert "period" in digest
        assert "sessions_analysed" in digest
        assert len(digest["sessions"]) == 1
        assert digest["sessions"][0]["key"] == "discord:999"

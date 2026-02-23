"""Tests for the heartbeat service."""

import asyncio

import pytest

from nanobot.heartbeat.service import (
    HEARTBEAT_OK_TOKEN,
    HEARTBEAT_PROMPT,
    HeartbeatService,
    _is_heartbeat_empty,
)


# ── _is_heartbeat_empty ───────────────────────────────────────────


def test_empty_none():
    assert _is_heartbeat_empty(None) is True


def test_empty_blank():
    assert _is_heartbeat_empty("") is True


def test_empty_invalid_toml():
    assert _is_heartbeat_empty("not valid toml {{{{") is True


def test_empty_no_tasks_section():
    content = "[heartbeat]\n# just a header\n"
    assert _is_heartbeat_empty(content) is True


def test_empty_tasks_sections_empty():
    content = "[tasks.active]\n[tasks.list]\n"
    assert _is_heartbeat_empty(content) is True


def test_not_empty_has_active_task():
    content = '[tasks.active]\ncheck_server = "Check server status"\n'
    assert _is_heartbeat_empty(content) is False


def test_not_empty_has_list_task():
    content = '[tasks.list]\nreport = "Send daily report"\n'
    assert _is_heartbeat_empty(content) is False


def test_empty_only_completed():
    """Completed tasks don't count as actionable."""
    content = '[tasks.completed]\nold_task = "Already done"\n'
    assert _is_heartbeat_empty(content) is True


# ── HeartbeatService ───────────────────────────────────────────────


def test_heartbeat_file_path(tmp_path):
    svc = HeartbeatService(workspace=tmp_path)
    assert svc.heartbeat_file == tmp_path / "HEARTBEAT.toml"


def test_read_heartbeat_file_missing(tmp_path):
    svc = HeartbeatService(workspace=tmp_path)
    assert svc._read_heartbeat_file() is None


def test_read_heartbeat_file_exists(tmp_path):
    hb = tmp_path / "HEARTBEAT.toml"
    hb.write_text('[tasks.active]\ncheck = "Do something"\n')
    svc = HeartbeatService(workspace=tmp_path)
    content = svc._read_heartbeat_file()
    assert "Do something" in content


@pytest.mark.asyncio
async def test_tick_skips_empty_file(tmp_path):
    """If HEARTBEAT.toml is empty, _tick should not call on_heartbeat."""
    called = False

    async def on_heartbeat(prompt):
        nonlocal called
        called = True
        return HEARTBEAT_OK_TOKEN

    svc = HeartbeatService(workspace=tmp_path, on_heartbeat=on_heartbeat)
    await svc._tick()
    assert called is False


@pytest.mark.asyncio
async def test_tick_calls_on_heartbeat(tmp_path):
    """If HEARTBEAT.toml has active tasks, _tick should call on_heartbeat."""
    hb = tmp_path / "HEARTBEAT.toml"
    hb.write_text('[tasks.active]\ncheck = "Check server status"\n')

    received_prompt = None

    async def on_heartbeat(prompt):
        nonlocal received_prompt
        received_prompt = prompt
        return HEARTBEAT_OK_TOKEN

    svc = HeartbeatService(workspace=tmp_path, on_heartbeat=on_heartbeat)
    await svc._tick()
    assert received_prompt == HEARTBEAT_PROMPT


@pytest.mark.asyncio
async def test_trigger_now(tmp_path):
    async def on_heartbeat(prompt):
        return "Done something"

    svc = HeartbeatService(workspace=tmp_path, on_heartbeat=on_heartbeat)
    result = await svc.trigger_now()
    assert result == "Done something"


@pytest.mark.asyncio
async def test_trigger_now_no_callback(tmp_path):
    svc = HeartbeatService(workspace=tmp_path, on_heartbeat=None)
    result = await svc.trigger_now()
    assert result is None


@pytest.mark.asyncio
async def test_start_disabled(tmp_path):
    svc = HeartbeatService(workspace=tmp_path, enabled=False)
    await svc.start()
    assert svc._task is None


def test_stop(tmp_path):
    svc = HeartbeatService(workspace=tmp_path)
    svc._running = True
    svc.stop()
    assert svc._running is False
    assert svc._task is None


def test_heartbeat_ok_token():
    """Verify the OK token matches what the service checks."""
    response = "HEARTBEAT_OK"
    normalized = response.upper().replace("_", "")
    expected = HEARTBEAT_OK_TOKEN.replace("_", "")
    assert expected in normalized

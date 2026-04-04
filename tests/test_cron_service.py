"""Tests for CronService: scheduling, tick, self-scheduling guard, auto-reload."""

import asyncio
import json
from pathlib import Path

import pytest

from blackcat.agent.tools.cron import CronTool
from blackcat.cron.service import CronService, _compute_next_run
from blackcat.cron.types import CronSchedule
from blackcat.utils.time import now_ms

# ── Schedule computation ──────────────────────────────────────────


def test_compute_next_run_at_future():
    now = now_ms()
    future = now + 60_000
    schedule = CronSchedule(kind="at", at_ms=future)
    assert _compute_next_run(schedule, now) == future


def test_compute_next_run_at_past():
    now = now_ms()
    past = now - 60_000
    schedule = CronSchedule(kind="at", at_ms=past)
    assert _compute_next_run(schedule, now) is None


def test_compute_next_run_every():
    now = now_ms()
    schedule = CronSchedule(kind="every", every_ms=5000)
    result = _compute_next_run(schedule, now)
    assert result == now + 5000


def test_compute_next_run_every_zero():
    schedule = CronSchedule(kind="every", every_ms=0)
    assert _compute_next_run(schedule, now_ms()) is None


def test_compute_next_run_cron_expr():
    schedule = CronSchedule(kind="cron", expr="* * * * *")  # every minute
    result = _compute_next_run(schedule, now_ms())
    assert result is not None
    assert result > now_ms() - 1000  # should be in the future


def test_compute_next_run_unknown_kind():
    schedule = CronSchedule(kind="unknown") # type: ignore
    assert _compute_next_run(schedule, now_ms()) is None


# ── CronService CRUD ──────────────────────────────────────────────


@pytest.fixture
def cron(tmp_path) -> CronService:
    store_path = tmp_path / "cron" / "jobs.json"
    return CronService(store_path)


def test_add_job(cron):
    job = cron.add_job(
        name="test",
        schedule=CronSchedule(kind="every", every_ms=10000),
        message="hello",
    )
    assert job.name == "test"
    assert job.id
    assert job.payload.message == "hello"


def test_list_jobs_empty(cron):
    assert cron.list_jobs() == []


def test_list_and_remove(cron):
    job = cron.add_job(
        name="cleanup",
        schedule=CronSchedule(kind="every", every_ms=5000),
        message="clean up",
    )
    assert len(cron.list_jobs()) == 1
    assert cron.remove_job(job.id) is True
    assert cron.list_jobs() == []


def test_remove_nonexistent(cron):
    assert cron.remove_job("nonexistent") is False


def test_add_job_persists(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    cron1 = CronService(store_path)
    cron1.add_job(
        name="persist",
        schedule=CronSchedule(kind="every", every_ms=1000),
        message="hi",
    )
    # New instance should load from disk
    cron2 = CronService(store_path)
    jobs = cron2.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].name == "persist"


def test_add_job_with_delivery(cron):
    job = cron.add_job(
        name="reminder",
        schedule=CronSchedule(kind="every", every_ms=5000),
        message="wake up",
        deliver=True,
        channel="telegram",
        to="12345",
    )
    assert job.payload.deliver is True
    assert job.payload.channel == "telegram"
    assert job.payload.to == "12345"


def test_add_at_job_delete_after(cron):
    future = now_ms() + 60_000
    job = cron.add_job(
        name="once",
        schedule=CronSchedule(kind="at", at_ms=future),
        message="one time",
        delete_after_run=True,
    )
    assert job.delete_after_run is True


# ── Auto-reload on external modification ──────────────────────────


def test_auto_reload_on_external_change(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    cron = CronService(store_path)
    cron.add_job(
        name="original",
        schedule=CronSchedule(kind="every", every_ms=1000),
        message="first",
    )
    assert len(cron.list_jobs()) == 1

    # Externally modify the file (simulating another process)
    data = json.loads(store_path.read_text())
    data["jobs"].append({
        "id": "ext-1",
        "name": "external",
        "enabled": True,
        "schedule": {"kind": "every", "everyMs": 2000},
        "payload": {"kind": "agent_turn", "message": "external job"},
        "state": {},
        "createdAtMs": 0,
        "updatedAtMs": 0,
        "deleteAfterRun": False,
    })
    store_path.write_text(json.dumps(data))

    # Force mtime difference (filesystem granularity)
    import os
    import time
    time.sleep(0.05)
    os.utime(store_path, (time.time() + 1, time.time() + 1))

    # Should reload and see both jobs
    jobs = cron.list_jobs()
    assert len(jobs) == 2
    names = {j.name for j in jobs}
    assert "external" in names


# ── CronTool self-scheduling guard ────────────────────────────────


def test_cron_tool_blocks_add_in_cron_context(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    tool = CronTool(service)
    tool.set_context("telegram", "123")

    token = tool.set_cron_context(True)
    try:
        result = asyncio.run(
            tool.execute(action="add", message="sneaky job", every_seconds=10)
        )
        assert "cannot schedule" in result.lower()
    finally:
        tool.reset_cron_context(token)


def test_cron_tool_allows_add_outside_context(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    tool = CronTool(service)
    tool.set_context("telegram", "123")

    result = asyncio.run(
        tool.execute(action="add", message="normal job", every_seconds=10)
    )
    assert "created" in result.lower()


def test_cron_tool_list_works_in_cron_context(tmp_path):
    """List and remove should still work even inside cron context."""
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path)
    tool = CronTool(service)

    token = tool.set_cron_context(True)
    try:
        result = asyncio.run(
            tool.execute(action="list")
        )
        assert "no scheduled" in result.lower() or "scheduled" in result.lower()
    finally:
        tool.reset_cron_context(token)


def test_cron_tool_context_reset():
    """After reset, add should work again."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store_path = Path(td) / "cron" / "jobs.json"
        service = CronService(store_path)
        tool = CronTool(service)
        tool.set_context("cli", "direct")

        token = tool.set_cron_context(True)
        tool.reset_cron_context(token)

        result = asyncio.run(
            tool.execute(action="add", message="after reset", every_seconds=5)
        )
        assert "created" in result.lower()


# ── Service status ────────────────────────────────────────────────


def test_service_status(cron):
    status = cron.status()
    assert status["enabled"] is False
    assert status["jobs"] == 0


def test_service_status_with_jobs(cron):
    cron.add_job(
        name="job1",
        schedule=CronSchedule(kind="every", every_ms=1000),
        message="hi",
    )
    status = cron.status()
    assert status["jobs"] == 1

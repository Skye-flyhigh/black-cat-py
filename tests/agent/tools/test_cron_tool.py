"""Tests for the CronTool (schedule, update, pause, resume jobs)."""

import pytest

from blackcat.agent.tools.cron import CronTool
from blackcat.cron.service import CronService


@pytest.fixture
def cron_service(tmp_path):
    """Create a cron service with a temporary store (not running)."""
    store_path = tmp_path / "cron_store.json"
    service = CronService(store_path=store_path)
    # Don't start the service - we're just testing the tool, not the scheduler
    return service


@pytest.fixture
def cron_tool(cron_service):
    """Create a cron tool with the service."""
    return CronTool(cron_service, default_timezone="UTC")


# ── Basic add/list/remove ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_job_every_seconds(cron_tool):
    """Add a recurring job with every_seconds."""
    cron_tool.set_context("cli", "test-user")
    result = await cron_tool.execute(
        action="add",
        message="Test reminder",
        every_seconds=60,
    )
    assert "Created job" in result
    assert "id:" in result


@pytest.mark.asyncio
async def test_add_job_cron_expr(cron_tool):
    """Add a scheduled job with cron expression."""
    cron_tool.set_context("cli", "test-user")
    result = await cron_tool.execute(
        action="add",
        message="Daily standup",
        cron_expr="0 9 * * *",
        tz="America/Vancouver",
    )
    assert "Created job" in result


@pytest.mark.asyncio
async def test_add_job_at(cron_tool):
    """Add a one-time job with 'at' datetime."""
    from datetime import datetime, timedelta

    cron_tool.set_context("cli", "test-user")
    at_time = (datetime.now() + timedelta(hours=1)).isoformat()
    result = await cron_tool.execute(
        action="add",
        message="One-time reminder",
        at=at_time,
    )
    assert "Created job" in result


@pytest.mark.asyncio
async def test_add_job_tool_name(cron_tool):
    """Add a job that executes a tool."""
    cron_tool.set_context("cli", "test-user")
    result = await cron_tool.execute(
        action="add",
        message='{"key": "value"}',
        every_seconds=300,
        tool_name="mcp_mnemo_decay",
    )
    assert "Created job" in result
    assert "execute mcp_mnemo_decay" in result


@pytest.mark.asyncio
async def test_list_jobs(cron_tool):
    """List all jobs."""
    cron_tool.set_context("cli", "test-user")
    await cron_tool.execute(action="add", message="Job 1", every_seconds=60)
    await cron_tool.execute(action="add", message="Job 2", every_seconds=120)

    result = await cron_tool.execute(action="list")
    assert "Scheduled jobs" in result
    assert "Job 1" in result
    assert "Job 2" in result


@pytest.mark.asyncio
async def test_list_jobs_empty(cron_tool):
    """List when no jobs exist."""
    result = await cron_tool.execute(action="list")
    assert "No scheduled jobs" in result


@pytest.mark.asyncio
async def test_remove_job(cron_tool):
    """Remove a job by ID."""
    cron_tool.set_context("cli", "test-user")
    # Use a far-future 'at' time so the job doesn't run immediately
    from datetime import datetime, timedelta
    at_time = (datetime.now() + timedelta(days=1)).isoformat()
    add_result = await cron_tool.execute(action="add", message="To remove", at=at_time)
    # Extract job ID from "Created job 'To remove' (id: xxx)"
    job_id = add_result.split("(id: ")[1].split(")")[0]

    result = await cron_tool.execute(action="remove", job_id=job_id)
    assert f"Removed job {job_id}" in result


# ── Update job ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_job_schedule(cron_tool):
    """Update a job's schedule."""
    cron_tool.set_context("cli", "test-user")
    add_result = await cron_tool.execute(action="add", message="Test", every_seconds=60)
    job_id = add_result.split("id: ")[1].split(")")[0]

    result = await cron_tool.execute(
        action="update",
        job_id=job_id,
        every_seconds=120,
    )
    assert "Updated job" in result
    assert "new id:" in result


@pytest.mark.asyncio
async def test_update_job_message(cron_tool):
    """Update a job's message."""
    cron_tool.set_context("cli", "test-user")
    add_result = await cron_tool.execute(action="add", message="Old message", every_seconds=60)
    job_id = add_result.split("id: ")[1].split(")")[0]

    result = await cron_tool.execute(
        action="update",
        job_id=job_id,
        message="New message",
    )
    assert "Updated job" in result


@pytest.mark.asyncio
async def test_update_job_not_found(cron_tool):
    """Update non-existent job returns error."""
    result = await cron_tool.execute(
        action="update",
        job_id="nonexistent-id",
        message="Test",
    )
    assert "Job nonexistent-id not found" in result


@pytest.mark.asyncio
async def test_update_job_missing_id(cron_tool):
    """Update without job_id returns error."""
    result = await cron_tool.execute(action="update", message="Test")
    assert "job_id is required for update" in result


# ── Pause/Resume ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_job(cron_tool):
    """Pause a job."""
    from datetime import datetime, timedelta

    cron_tool.set_context("cli", "test-user")
    # Use a far-future 'at' time so the job doesn't run immediately
    at_time = (datetime.now() + timedelta(days=1)).isoformat()
    add_result = await cron_tool.execute(action="add", message="To pause", at=at_time)
    # Extract job ID from "Created job 'To pause' (id: xxx)"
    job_id = add_result.split("(id: ")[1].split(")")[0]

    result = await cron_tool.execute(action="pause", job_id=job_id)
    assert f"Paused job {job_id}" in result

    # Verify job still exists in list (pause changes ID due to remove/add pattern)
    list_result = await cron_tool.execute(action="list")
    assert "To pause" in list_result or "Scheduled jobs" in list_result


@pytest.mark.asyncio
async def test_resume_job(cron_tool):
    """Resume a paused job."""
    from datetime import datetime, timedelta

    cron_tool.set_context("cli", "test-user")
    # Use a far-future 'at' time so the job doesn't run immediately
    at_time = (datetime.now() + timedelta(days=1)).isoformat()
    add_result = await cron_tool.execute(action="add", message="To resume", at=at_time)
    # Extract job ID from "Created job 'To resume' (id: xxx)"
    job_id = add_result.split("(id: ")[1].split(")")[0]

    pause_result = await cron_tool.execute(action="pause", job_id=job_id)
    # Extract new job ID from pause result
    new_job_id = pause_result.split("(id: ")[1].split(")")[0]

    result = await cron_tool.execute(action="resume", job_id=new_job_id)
    assert f"Resumed job {new_job_id}" in result

    # Verify job exists in list
    list_result = await cron_tool.execute(action="list")
    assert "To resume" in list_result


@pytest.mark.asyncio
async def test_pause_job_not_found(cron_tool):
    """Pause non-existent job returns error."""
    result = await cron_tool.execute(action="pause", job_id="nonexistent")
    assert "Job nonexistent not found" in result


@pytest.mark.asyncio
async def test_resume_job_not_found(cron_tool):
    """Resume non-existent job returns error."""
    result = await cron_tool.execute(action="resume", job_id="nonexistent")
    assert "Job nonexistent not found" in result


@pytest.mark.asyncio
async def test_pause_missing_id(cron_tool):
    """Pause without job_id returns error."""
    result = await cron_tool.execute(action="pause")
    assert "job_id is required for pause" in result


@pytest.mark.asyncio
async def test_resume_missing_id(cron_tool):
    """Resume without job_id returns error."""
    result = await cron_tool.execute(action="resume")
    assert "job_id is required for resume" in result


# ── Context validation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_job_no_context(cron_tool):
    """Add job without channel/chat_id context fails."""
    result = await cron_tool.execute(action="add", message="Test", every_seconds=60)
    assert "no session context" in result


@pytest.mark.asyncio
async def test_add_job_from_cron_context(cron_service):
    """Cannot schedule jobs from within a cron job callback."""
    cron_tool = CronTool(cron_service)
    cron_tool.set_cron_context(True)
    cron_tool.set_context("cli", "test-user")

    result = await cron_tool.execute(action="add", message="Test", every_seconds=60)
    assert "cannot schedule new jobs from within a cron job" in result


# ── Validation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_job_missing_message(cron_tool):
    """Add job without message fails."""
    cron_tool.set_context("cli", "test-user")
    result = await cron_tool.execute(action="add", every_seconds=60)
    assert "message or tool_name is required" in result


@pytest.mark.asyncio
async def test_add_job_invalid_timezone(cron_tool):
    """Add job with invalid timezone fails."""
    cron_tool.set_context("cli", "test-user")
    result = await cron_tool.execute(
        action="add",
        message="Test",
        cron_expr="0 9 * * *",
        tz="Invalid/Timezone",
    )
    assert "unknown timezone" in result


@pytest.mark.asyncio
async def test_add_job_no_schedule(cron_tool):
    """Add job without schedule fails."""
    cron_tool.set_context("cli", "test-user")
    result = await cron_tool.execute(action="add", message="Test")
    assert "either every_seconds, cron_expr, or at is required" in result


# ── Unknown action ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_action(cron_tool):
    """Unknown action returns error."""
    result = await cron_tool.execute(action="invalid_action")
    assert "Unknown action" in result

"""Cron tool for scheduling reminders and tasks."""

import json
from contextvars import ContextVar, Token
from datetime import datetime
from typing import Any

from blackcat.agent.tools.base import Tool
from blackcat.cron.service import CronService
from blackcat.cron.types import CronJob, CronSchedule


class CronTool(Tool):
    """Tool to schedule reminders, recurring tasks, and tool executions."""

    name = "cron"
    description = "Schedule reminders, recurring tasks, and tool executions. Actions: add, list, remove, update, pause, resume."
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "remove", "update", "pause", "resume"],
                "description": "Action to perform",
            },
            "message": {"type": "string", "description": "Reminder message (for add/update)"},
            "every_seconds": {
                "type": "integer",
                "description": "Interval in seconds (for recurring tasks)",
            },
            "cron_expr": {
                "type": "string",
                "description": "Cron expression like '0 9 * * *' (for scheduled tasks)",
            },
            "tz": {
                "type": "string",
                "description": "IANA timezone for cron expressions (e.g. 'America/Vancouver')",
            },
            "at": {
                "type": "string",
                "description": "ISO datetime for one-time execution (e.g. '2026-03-01T09:00:00')",
            },
            "job_id": {"type": "string", "description": "Job ID (for remove, update, pause, resume)"},
            "tool_name": {
                "type": "string",
                "description": "Name of tool to execute (e.g., 'mcp_mnemo_decay'). If set, message becomes tool parameters JSON",
            },
            "metadata": {
                "type": "object",
                "description": "Arbitrary key-value metadata to store with the job",
            },
            "verbose": {
                "type": "boolean",
                "description": "Show detailed info in list output",
            },
        },
        "required": ["action"],
    }

    def __init__(self, cron_service: CronService):
        self._cron = cron_service
        self._channel = ""
        self._chat_id = ""
        self._in_cron_context: ContextVar[bool] = ContextVar("cron_in_context", default=False)

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the current session context for delivery."""
        self._channel = channel
        self._chat_id = chat_id

    def set_cron_context(self, active: bool) -> Token[bool]:
        """Mark whether the tool is executing inside a cron job callback."""
        return self._in_cron_context.set(active)

    def reset_cron_context(self, token: Any) -> None:
        """Restore previous cron context."""
        self._in_cron_context.reset(token)

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs["action"]
        message: str = kwargs.get("message", "")
        every_seconds: int | None = kwargs.get("every_seconds")
        cron_expr: str | None = kwargs.get("cron_expr")
        tz: str | None = kwargs.get("tz")
        at: str | None = kwargs.get("at")
        job_id: str | None = kwargs.get("job_id")
        tool_name: str | None = kwargs.get("tool_name")
        metadata: dict | None = kwargs.get("metadata")
        verbose: bool = kwargs.get("verbose", False)

        if action == "add":
            if self._in_cron_context.get():
                return "Error: cannot schedule new jobs from within a cron job execution"
            return self._add_job(message, every_seconds, cron_expr, tz, at, tool_name, metadata)
        elif action == "list":
            return self._list_jobs(verbose)
        elif action == "remove":
            return self._remove_job(job_id)
        elif action == "update":
            return self._update_job(job_id, message, every_seconds, cron_expr, tz, at, tool_name, metadata)
        elif action == "pause":
            return self._pause_job(job_id)
        elif action == "resume":
            return self._resume_job(job_id)
        return f"Unknown action: {action}"

    def _encode_tool_message(self, message: str, tool_name: str) -> str:
        """Encode a message as a tool call JSON payload."""
        try:
            params = json.loads(message) if message else {}
        except json.JSONDecodeError:
            params = {"message": message} if message else {}
        return json.dumps({"tool": tool_name, "params": params})

    def _safe_metadata(self, metadata: dict | None) -> dict:
        """Return a copy of metadata, or empty dict if None."""
        return dict(metadata) if metadata else {}

    def _require_job(
        self, job_id: str | None, action: str
    ) -> tuple[CronJob, None] | tuple[None, str]:
        """Fetch a job or return an error. Returns (job, None) or (None, error_message)."""
        if not job_id or job_id is None:
            return None, f"Error: job_id is required for {action}"
        job = self._cron.get_job(job_id)
        if not job:
            return None, f"Job {job_id} not found"
        return job, None

    def _build_schedule(
        self,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
    ) -> tuple[CronSchedule, bool]:
        """Build a CronSchedule from parameters. Returns (schedule, delete_after_run)."""
        if tz and not cron_expr:
            raise ValueError("tz can only be used with cron_expr")
        if tz:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(tz)
            except (KeyError, Exception) as e:
                raise ValueError(f"unknown timezone '{tz}'") from e

        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
        elif at:
            dt = datetime.fromisoformat(at)
            at_ms = int(dt.timestamp() * 1000)
            schedule = CronSchedule(kind="at", at_ms=at_ms)
            delete_after = True
        else:
            raise ValueError("either every_seconds, cron_expr, or at is required")

        return schedule, delete_after

    def _add_job(
        self,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
        tool_name: str | None,
        metadata: dict | None,
    ) -> str:
        if not message and not tool_name:
            return "Error: message or tool_name is required for add"
        if not self._channel or not self._chat_id:
            return "Error: no session context (channel/chat_id)"

        try:
            schedule, delete_after = self._build_schedule(every_seconds, cron_expr, tz, at)
        except ValueError as e:
            return f"Error: {e}"

        # Encode tool execution if specified
        job_message = self._encode_tool_message(message, tool_name) if tool_name else message

        # Build metadata with internal flags
        job_metadata = self._safe_metadata(metadata)
        job_metadata["_paused"] = False
        if tool_name:
            job_metadata["_tool_name"] = tool_name

        job = self._cron.add_job(
            name=message[:30] if message else (tool_name or "untitled"),
            schedule=schedule,
            message=job_message,
            deliver=True,
            channel=self._channel,
            to=self._chat_id,
            delete_after_run=delete_after,
            metadata=job_metadata,
        )

        tool_info = f" → execute {tool_name}" if tool_name else ""
        return f"Created job '{job.name}' (id: {job.id}){tool_info}"

    def _list_jobs(self, verbose: bool = False) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."

        lines = []
        for j in jobs:
            meta = self._safe_metadata(j.metadata)

            # Basic info
            is_paused = meta.get("_paused", False)
            status = "⏸️ " if is_paused else "▶️ "
            lines.append(f"{status}{j.name} (id: {j.id}, {j.schedule.kind})")

            if verbose:
                # Schedule details
                if j.schedule.kind == "cron":
                    tz_info = f" ({j.schedule.tz})" if j.schedule.tz else ""
                    lines.append(f"   schedule: cron '{j.schedule.expr}'{tz_info}")
                elif j.schedule.kind == "every":
                    secs = (j.schedule.every_ms // 1000) if j.schedule.every_ms is not None else 0
                    lines.append(f"   schedule: every {secs}s")
                elif j.schedule.kind == "at":
                    if j.schedule.at_ms is not None:
                        dt = datetime.fromtimestamp(j.schedule.at_ms / 1000)
                        lines.append(f"   schedule: at {dt.isoformat()}")
                    else:
                        lines.append("   schedule: at (time not set)")

                # Tool execution info
                tool_name = meta.get("_tool_name")
                if tool_name:
                    lines.append(f"   action: execute tool '{tool_name}'")

                # User metadata (excluding internal flags)
                user_meta = {k: v for k, v in meta.items() if not k.startswith("_")}
                if user_meta:
                    lines.append(f"   metadata: {json.dumps(user_meta)}")

        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        if self._cron.remove_job(job_id):
            return f"Removed job {job_id}"
        return f"Job {job_id} not found"

    def _update_job(
        self,
        job_id: str | None,
        message: str | None,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
        tool_name: str | None,
        metadata: dict | None,
    ) -> str:
        """Update an existing job. Only specified fields are changed."""
        job, error = self._require_job(job_id, "update")
        if error:
            return error

        if job is not None and job_id is not None:
            # Build new schedule if any timing params provided
            new_schedule = None
            delete_after = job.delete_after_run
            if any(p is not None for p in [every_seconds, cron_expr, at]):
                try:
                    new_schedule, delete_after = self._build_schedule(every_seconds, cron_expr, tz, at)
                except ValueError as e:
                    return f"Error: {e}"

            # Update message (preserve tool encoding if applicable)
            new_message = job.payload.message if job.payload else ""

            # Update metadata
            new_metadata = self._safe_metadata(job.metadata)
            if metadata:
                new_metadata.update(metadata)
            if tool_name:
                new_metadata["_tool_name"] = tool_name
            # Preserve pause state
            if job.metadata and "_paused" in job.metadata:
                new_metadata["_paused"] = job.metadata["_paused"]

            # Remove old and add new (since CronService likely doesn't support true updates)
            self._cron.remove_job(job_id)
            new_job = self._cron.add_job(
                name=job.name,
                schedule=new_schedule or job.schedule,
                message=new_message,
                deliver=job.payload.deliver,
                channel=job.payload.channel,
                to=job.payload.to,
                delete_after_run=delete_after,
                metadata=new_metadata,
            )

            return f"Updated job (new id: {new_job.id})"
        else:
            return "No job to update"

    def _pause_job(self, job_id: str | None) -> str:
        """Pause a job without removing it."""
        job, error = self._require_job(job_id, "pause")
        if error:
            return error

        if job_id is not None and job is not None:
            self._cron.enable_job(job_id, enabled=False)

            # Mark as paused in metadata
            if job.metadata is None:
                job.metadata = {}
            job.metadata["_paused"] = True
            self._cron._save_store()
            return f"Paused job {job_id}"
        else:
            return "No job to pause"

    def _resume_job(self, job_id: str | None) -> str:
        """Resume a paused job."""
        job, error = self._require_job(job_id, "resume")
        if error:
            return error

        if job is not None and job_id is not None:
            self._cron.enable_job(job_id, enabled=True)
            if job.metadata is None:
                job.metadata = {}
            job.metadata["_paused"] = False
            self._cron._save_store()
            return f"Resumed job {job_id}"
        else:
            return "No job to resume"

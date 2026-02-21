"""Daily summary service - consolidates the day's conversations into memory."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.memory import Journal
from nanobot.agent.summarizer import Summarizer
from nanobot.session.manager import SessionManager

if TYPE_CHECKING:
    from nanobot.agent.memory_manager import Memory

# Default: run at 3am
DEFAULT_SUMMARY_HOUR = 3


class DailySummaryService:
    """
    Daily summary service that consolidates conversations into memory.

    Runs once per day at a configured hour:
    1. Gathers all sessions with activity from the previous day
    2. Summarizes each session
    3. Extracts important facts
    4. Appends summaries to daily memory notes
    5. Updates long-term memory with extracted facts
    """

    def __init__(
        self,
        workspace: Path,
        summarizer: Summarizer,
        session_manager: SessionManager,
        summary_hour: int = DEFAULT_SUMMARY_HOUR,
        enabled: bool = True,
        memory: "Memory | None" = None,
    ):
        self.workspace = workspace
        self.summarizer = summarizer
        self.session_manager = session_manager
        self.summary_hour = summary_hour
        self.enabled = enabled
        self.journal = Journal(workspace)
        self.memory = memory  # Semantic vector memory
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_run_date: str | None = None

    async def start(self) -> None:
        """Start the daily summary service."""
        if not self.enabled:
            logger.info("Daily summary service disabled")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Daily summary service started (runs at {self.summary_hour:02d}:00)")

    def stop(self) -> None:
        """Stop the daily summary service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """Main loop - checks every hour if it's time to run."""
        while self._running:
            try:
                await asyncio.sleep(60 * 60)  # Check every hour

                if self._running and self._should_run():
                    await self._run_daily_summary()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily summary error: {e}")

    def _should_run(self) -> bool:
        """Check if we should run the daily summary now."""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        # Already ran today?
        if self._last_run_date == today:
            return False

        # Is it the right hour?
        return now.hour == self.summary_hour

    async def _run_daily_summary(self) -> None:
        """Execute the daily summary consolidation."""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        logger.info(f"Running daily summary for {yesterday}")
        self._last_run_date = today

        # Get all sessions
        sessions = self.session_manager.list_sessions()

        if not sessions:
            logger.info("No sessions to summarize")
            return

        all_summaries = []
        all_facts = []

        for session_info in sessions:
            session_key = session_info["key"]
            session = self.session_manager.get_or_create(session_key)
            messages = session.get_history()

            # Skip empty sessions
            if len(messages) < 2:
                continue

            # Filter to messages from yesterday (if we can determine timestamps)
            # For now, summarize all - session manager doesn't track timestamps yet
            # TODO: Add timestamp filtering when session format supports it

            try:
                result = await self.summarizer.summarize_session(messages, session_key)

                if result["summary"]:
                    all_summaries.append(f"### {session_key}\n{result['summary']}")

                if result["facts"]:
                    all_facts.append(result["facts"])

            except Exception as e:
                logger.error(f"Failed to summarize session {session_key}: {e}")
                continue

        # Write daily summary to memory notes
        if all_summaries:
            summary_content = "## Conversation Summaries\n\n" + "\n\n".join(all_summaries)
            self.journal.append_today(summary_content)
            logger.info(f"Appended {len(all_summaries)} session summaries to daily notes")

        # Update long-term memory with facts
        if all_facts:
            await self._update_long_term_memory(all_facts)

        logger.info(
            f"Daily summary complete: {len(all_summaries)} sessions, {len(all_facts)} fact extractions"
        )

    async def _update_long_term_memory(self, facts_list: list[str]) -> None:
        """Append extracted facts to long-term memory (journal + vector)."""
        existing = self.journal.read_long_term()

        # Combine all facts
        new_facts = "\n".join(facts_list)
        timestamp = datetime.now().strftime("%Y-%m-%d")

        update = f"\n\n## Updates from {timestamp}\n\n{new_facts}"

        self.journal.write_long_term(existing + update)
        logger.info(f"Updated journal long-term memory with facts from {timestamp}")

        # Also store in vector memory if available
        if self.memory:
            stored_count = 0
            for fact in facts_list:
                # Each fact block may contain multiple lines - store each line as a fact
                for line in fact.strip().split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):  # Skip empty lines and headers
                        try:
                            await self.memory.add(
                                content=line,
                                author="daily_summary",
                                source="consolidation",
                                tag="default",
                            )
                            stored_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to store fact in vector memory: {e}")

            if stored_count > 0:
                logger.info(f"Stored {stored_count} facts in vector memory")

    async def run_now(self) -> dict[str, Any]:
        """Manually trigger the daily summary (for testing)."""
        logger.info("Manual daily summary triggered")

        # Get session count before
        sessions = self.session_manager.list_sessions()
        session_count = len(sessions)

        await self._run_daily_summary()

        return {
            "sessions_processed": session_count,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }

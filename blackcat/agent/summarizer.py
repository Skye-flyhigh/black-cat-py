"""Summarizer utility for context compaction and memory consolidation."""

from datetime import datetime
from typing import Any

from loguru import logger

from blackcat.providers.base import LLMProvider
from blackcat.utils.time import last_24h


class Summarizer:
    """
    Summarizes conversations and extracts key information.

    Used by:
    - ContextManager for sliding window compaction
    - Daily cron for session consolidation
    """

    # Default prompt for conversation summarization
    SUMMARIZE_PROMPT = """You are blackcat internal summariser to help to reduce context window.
    Summarize the following conversation concisely on the following points:
- Key decisions made
- Important facts learned
- Action items or commitments
- Unresolved questions

Keep short, concised but contextual. blackcat needs to understand what's going on. No styling."""

    # Prompt for extracting long-term facts
    EXTRACT_FACTS_PROMPT = """Extract only the important long-term facts from this conversation.
These should be things worth remembering permanently:
- User preferences or personal information
- Project details or technical decisions
- Commitments or recurring topics
- Corrections to previous knowledge

Return only facts worth keeping. If nothing is worth remembering long-term, say "Nothing to extract."
Format as bullet points."""

    def __init__(
        self,
        provider: LLMProvider,
        model: str | None = None,
        timeout: int | None = 30,
    ):
        """
        Initialize the summarizer.

        Args:
            provider: LLM provider for generating summaries.
            model: Model to use (defaults to provider's default, but ideally a fast/cheap one).
            timeout: Timeout for summarization calls.
        """
        self.provider = provider
        self.model = model or provider.get_default_model()
        self.timeout = timeout

    async def summarize_messages(
        self,
        messages: list[dict[str, Any]],
        prompt: str | None = None,
    ) -> str:
        """
        Summarize a list of messages.

        Args:
            messages: Messages to summarize (in OpenAI format).
            prompt: Custom summarization prompt (uses default if None).

        Returns:
            Summary string.
        """
        if not messages:
            return ""

        # Format messages for summarization
        formatted = self._format_messages_for_summary(messages)

        if not formatted.strip():
            return ""

        system_prompt = prompt or self.SUMMARIZE_PROMPT

        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": formatted},
                ],
                model=self.model,
                max_tokens=1024,
                temperature=0.3,  # Lower temperature for factual summarization
                timeout=self.timeout,
            )
            summary = response.content or ""
            return summary.strip()

        except Exception as e:
            logger.error("Summarization failed: {}", e)
            # Fallback: return truncated original if summarization fails
            return f"[Summary unavailable: {len(messages)} messages]"

    async def extract_facts(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """
        Extract long-term facts worth remembering from messages.

        Args:
            messages: Messages to extract facts from.

        Returns:
            Extracted facts or empty string if nothing worth keeping.
        """
        if not messages:
            return ""

        formatted = self._format_messages_for_summary(messages)

        if not formatted.strip():
            logger.debug("No formatted content for fact extraction ({} messages)", len(messages))
            return ""

        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": self.EXTRACT_FACTS_PROMPT},
                    {"role": "user", "content": formatted},
                ],
                model=self.model,
                max_tokens=512,
                temperature=0.2,
                timeout=self.timeout,
            )

            facts = response.content or ""

            # Check for "nothing to extract" responses
            if "nothing to extract" in facts.lower() or not facts.strip():
                return ""

            logger.debug("Extracted facts from {} messages", len(messages))
            return facts.strip()

        except Exception as e:
            logger.error("Fact extraction failed: {}", e)
            return ""

    async def summarize_session(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
    ) -> dict[str, str]:
        """
        Summarize a full session for daily consolidation.

        Args:
            messages: All messages from the session.
            session_key: Session identifier for logging.

        Returns:
            Dict with 'summary' and 'facts' keys.
        """
        logger.info("Summarizing session {} ({} messages)", session_key, len(messages))

        yesterday = last_24h(datetime.now())
        filtered_messages = []

        for m in messages:
            ts_raw = m.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts_clean: str = ts_raw.replace("Z", "+00:00")
                ts_dt = datetime.fromisoformat(ts_clean)

                msg_date = ts_dt.date()

                if msg_date == yesterday:
                    filtered_messages.append(m)
            except (ValueError, TypeError):
                logger.warning("Invalide timestamp format: %s in message %s", ts_raw, m.get("id", "unknown"))
                continue

        if not filtered_messages:
            return {"summary": "No message to summarize", "facts": ""}

        summary = await self.summarize_messages(filtered_messages)
        facts = await self.extract_facts(filtered_messages)

        return {
            "summary": summary,
            "facts": facts,
        }

    def _format_messages_for_summary(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """Format messages into readable text for summarization."""
        lines = []
        try:
            for msg in messages:
                author = msg.get("author", "")
                role = msg.get("role", "unknown")
                content = msg.get("content", "")

                # Skip system messages and tool calls for summarization
                if role == "system":
                    continue
                if role == "tool":
                    continue
                if not content:
                    continue

                # Format based on role
                if author:
                    lines.append(f"{author}: {content}")
                elif role == "user":
                    lines.append(f"User: {content}")
                elif role == "assistant":
                    lines.append(f"Assistant: {content}")
                else:
                    lines.append(f"{role}: {content}")
        except Exception as e:
            logger.error("Failed to format messages for summarization: {}", e)
            return ""

        return "\n\n".join(lines)

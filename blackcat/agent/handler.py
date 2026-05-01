"""Message handler: processes a single inbound message."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from blackcat.agent.tools.ask import (
    ask_user_options_from_messages,
    ask_user_outbound,
    ask_user_tool_result_messages,
    pending_ask_user_id,
)
from blackcat.agent.tools.message import MessageTool
from blackcat.bus.events import OutboundMessage
from blackcat.command import CommandContext
from blackcat.config.schema import Config
from blackcat.utils.document import extract_documents
from blackcat.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

if TYPE_CHECKING:
    from blackcat.agent.loop import AgentLoop
    from blackcat.bus.events import InboundMessage


class MessageHandler:
    """
    Handles a single inbound message through the agent.

    Responsibilities:
    - Session management (get/create, checkpoint restore, pending user turn)
    - Auto-compaction and consolidation
    - Slash command dispatch
    - Context building
    - LLM iteration coordination
    - Turn persistence (save history, clear checkpoints)
    - Response formatting (including ask_user options)

    Delegates to AgentLoop for:
    - Tool execution (ToolRegistry)
    - LLM provider calls (AgentRunner)
    - Runtime checkpoint save/restore helpers
    """

    __slots__ = ("_loop", "_msg", "config")

    def __init__(self, loop: "AgentLoop", msg: "InboundMessage", config: Config) -> None:
        self._loop = loop
        self._msg = msg
        self.config = config

    async def process(
        self,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: Any | None = None,
    ) -> OutboundMessage | None:
        """
        Process the inbound message and return a response.

        Returns:
            OutboundMessage to send, or None if no response needed.
        """
        loop = self._loop
        msg = self._msg

        loop._refresh_provider_snapshot()
        # System messages (subagent follow-ups, background tasks)
        if msg.channel == "system":
            return await self._process_system_message(msg, pending_queue)

        # Extract document text from media (PDF, DOCX, etc.)
        if msg.media:
            new_content, image_only = extract_documents(msg.content, msg.media)
            msg = msg.__class__(
                channel=msg.channel,
                sender_id=msg.sender_id,
                chat_id=msg.chat_id,
                content=new_content,
                media=image_only,
                metadata=msg.metadata,
            )

        key = session_key or msg.session_key
        session = loop.sessions.get_or_create(key)

        # Restore checkpoint state from crash recovery
        if loop._restore_runtime_checkpoint(session):
            loop.sessions.save(session)
        if loop._restore_pending_user_turn(session):
            loop.sessions.save(session)

        # Prepare session (auto-compact if idle)
        session, pending = loop.auto_compact.prepare_session(session, key)

        # Slash commands
        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=loop)
        if result := await loop.commands.dispatch(ctx):
            return result

        # Token-budget consolidation
        await loop.consolidator.maybe_consolidate_by_tokens(session, session_summary=pending)

        # Set tool context for this turn
        loop._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))

        # Reset per-turn MessageTool tracking
        message_tool = loop.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.start_turn()

        history = session.get_history(max_messages=loop._max_messages, include_timestamps=True)

        # Handle pending ask_user response
        pending_ask_id = pending_ask_user_id(history)
        author = self.config.resolve_author(msg.sender_id, msg.channel)
        if pending_ask_id:
            system_prompt = await loop.context.build_system_prompt(
                author=author,
                channel=msg.channel,
            )
            initial_messages = ask_user_tool_result_messages(
                system_prompt,
                history,
                pending_ask_id,
                msg.content,
            )
        else:
            initial_messages = await loop.context.build_messages(
                history=history,
                current_message=msg.content,
                media=msg.media if msg.media else None,
                channel=msg.channel,
                chat_id=loop._runtime_chat_id(msg),
                author=author,
            )

        # Build progress callback
        async def _bus_progress(
            content: str,
            *,
            tool_hint: bool = False,
            tool_events: list[dict[str, Any]] | None = None,
        ) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            if tool_events:
                meta["_tool_events"] = tool_events
            await loop.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        async def _on_retry_wait(content: str) -> None:
            meta = dict(msg.metadata or {})
            meta["_retry_wait"] = True
            await loop.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        # Persist the triggering user message up front so a mid-turn crash
        # doesn't silently lose the prompt on recovery.
        user_persisted_early = False
        media_paths = [p for p in (msg.media or []) if isinstance(p, str) and p]
        has_text = isinstance(msg.content, str) and msg.content.strip()
        if not pending_ask_id and (has_text or media_paths):
            extra: dict[str, Any] = {"media": list(media_paths)} if media_paths else {}
            text = msg.content if isinstance(msg.content, str) else ""
            session.add_message("user", text, **extra)
            loop._mark_pending_user_turn(session)
            loop.sessions.save(session)
            user_persisted_early = True

        # Run agent loop
        final_content, _, all_msgs, stop_reason, had_injections = await loop._run_agent_loop(
            initial_messages,
            on_progress=on_progress or _bus_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_retry_wait=_on_retry_wait,
            session=session,
            channel=msg.channel,
            chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
            pending_queue=pending_queue,
        )

        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        # Skip the already-persisted user message when saving the turn
        save_skip = 1 + len(history) + (1 if user_persisted_early else 0)
        loop._save_turn(session, all_msgs, save_skip)
        loop._clear_pending_user_turn(session)
        loop._clear_runtime_checkpoint(session)
        loop.sessions.save(session)
        loop._schedule_background(loop.consolidator.maybe_consolidate_by_tokens(session))
        options = ask_user_options_from_messages(all_msgs) if stop_reason == "ask_user" else []
        content, buttons = ask_user_outbound(
            final_content or "Background task completed.",
            options,
            msg.channel
        )

        # Suppress response if MessageTool already sent to this target
        if (mt := loop.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        meta = dict(msg.metadata or {})
        final_content, buttons = ask_user_outbound(
            final_content,
            ask_user_options_from_messages(all_msgs) if stop_reason == "ask_user" else [],
            msg.channel,
        )
        if on_stream is not None and stop_reason not in {"ask_user", "error"}:
            meta["_streamed"] = True
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata=meta,
            buttons=buttons,
        )

    async def _process_system_message(
        self,
        msg: "InboundMessage",
        pending_queue: Any | None = None,
    ) -> OutboundMessage | None:
        """Process system message (subagent follow-up, background task)."""
        loop = self._loop

        # Parse origin from chat_id ("channel:chat_id")
        channel, chat_id = (
            msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
        )
        logger.info("Processing system message from {}", msg.sender_id)
        # Use session_key_override if provided (for thread-scoped sessions)
        key = getattr(msg, "session_key_override", None) or f"{channel}:{chat_id}"
        session = loop.sessions.get_or_create(key)

        # Restore checkpoint state from crash recovery
        if loop._restore_runtime_checkpoint(session):
            loop.sessions.save(session)
        if loop._restore_pending_user_turn(session):
            loop.sessions.save(session)

        # Prepare session (auto-compact if idle)
        session, pending = loop.auto_compact.prepare_session(session, key)

        # Token-budget consolidation
        await loop.consolidator.maybe_consolidate_by_tokens(session)

        # Persist subagent follow-ups before prompt assembly
        is_subagent = msg.sender_id == "subagent"
        if is_subagent and loop._persist_subagent_followup(session, msg):
            loop.sessions.save(session)

        # Set tool context for this turn
        loop._set_tool_context(
            channel, chat_id, msg.metadata.get("message_id"),
            metadata=msg.metadata.get("channel_meta"),
            session_key=getattr(msg, "session_key_override", None),
        )

        history = session.get_history(max_messages=loop._max_messages, include_timestamps=True)

        # Subagent content is already in `history`; passing it again would double-project
        # System messages use "system" as author since they're not from a user
        messages = await loop.context.build_messages(
            history=history,
            current_message="" if is_subagent else msg.content,
            channel=channel,
            chat_id=chat_id,
            author="system",
        )

        final_content, _, all_msgs, stop_reason, _ = await loop._run_agent_loop(
            messages,
            session=session,
            channel=channel,
            chat_id=chat_id,
            message_id=msg.metadata.get("message_id"),
            pending_queue=pending_queue,
        )

        loop._save_turn(session, all_msgs, 1 + len(history))
        loop._clear_runtime_checkpoint(session)
        loop.sessions.save(session)
        loop._schedule_background(loop.consolidator.maybe_consolidate_by_tokens(session))

        # Restore channel metadata from session context for outbound routing
        # (e.g., Slack thread_ts from session_key like "slack:C123:1700.42")
        outbound_meta: dict = {}
        session_key = getattr(msg, "session_key_override", None) or f"{channel}:{chat_id}"
        if channel == "slack" and ":" in session_key:
            parts = session_key.split(":")
            if len(parts) >= 3:
                outbound_meta["slack"] = {"thread_ts": parts[2]}

        options = ask_user_options_from_messages(all_msgs) if stop_reason == "ask_user" else []
        content, buttons = ask_user_outbound(
            final_content or "Background task completed.",
            options,
            channel,
        )
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            metadata=outbound_meta,
            buttons=buttons,
        )

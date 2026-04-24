"""Message handler: processes a single inbound message."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from blackcat.agent.tools.message import MessageTool
from blackcat.bus.events import OutboundMessage
from blackcat.utils.document import extract_documents

if TYPE_CHECKING:
    from blackcat.agent.loop import AgentLoop
    from blackcat.bus.events import InboundMessage
    from websockets.asyncio.queue import Queue


class MessageHandler:
    """
    Handles a single inbound message through the agent.

    Responsibilities:
    - Resolve message origin (system vs channel)
    - Extract document text from media
    - Format final response

    NOT responsible for:
    - Session management (AgentLoop)
    - Checkpoint save/restore (AgentLoop)
    - Context building (ContextManager)
    - LLM iteration (AgentRunner)
    - Tool execution (ToolRegistry)
    - Compaction/persistence (AgentLoop)
    """

    __slots__ = ("_loop", "_msg")

    def __init__(self, loop: "AgentLoop", msg: "InboundMessage") -> None:
        self._loop = loop
        self._msg = msg

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

        # Parse origin
        is_system = msg.channel == "system"
        if is_system:
            channel, chat_id = self._parse_system_origin(msg.chat_id)
            logger.info("Processing system message from {}", msg.sender_id)
        else:
            channel, chat_id = msg.channel, msg.chat_id

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

        # Set tool context for this turn
        loop._set_tool_context(channel, chat_id, msg.metadata.get("message_id") if msg.metadata else None)

        # Reset per-turn MessageTool tracking
        message_tool = loop.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.start_turn()

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
                    channel=channel,
                    chat_id=chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        # Resolve author for context building
        author = self._resolve_author(msg.sender_id, channel) if not is_system else msg.sender_id

        # Build context messages via ContextManager
        messages = await loop.context.build_messages(
            history=[],  # _dispatch will inject full history
            current_message="" if is_system else msg.content,
            author=author,
            channel=channel,
            chat_id=chat_id,
            media=msg.media if msg.media and not is_system else None,
            use_structured_system=loop.provider.supports_prompt_caching,
        )

        # Run agent loop
        result = await loop._run_agent_loop(
            messages,
            on_progress=on_progress or _bus_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            session=None,  # _dispatch injects session
            channel=channel,
            chat_id=chat_id,
            message_id=msg.metadata.get("message_id") if msg.metadata else None,
        )
        final_content = result[0]

        # Handle empty final response
        if final_content is None or not final_content.strip():
            from blackcat.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        # Suppress response if MessageTool already sent to this target
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return None

        metadata = msg.metadata or {}
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=final_content,
            reply_to=str(metadata.get("reply_to")) if metadata.get("reply_to") else None,
            metadata=metadata,
        )

    def _parse_system_origin(self, chat_id: str) -> tuple[str, str]:
        """Parse system message origin from chat_id (format: 'channel:chat_id')."""
        from blackcat.utils.helpers import parse_session_key
        if ":" in chat_id:
            return parse_session_key(chat_id)
        return "cli", chat_id

    def _resolve_author(self, sender_id: str, channel: str) -> str:
        """Resolve sender_id to author name using config."""
        if self._loop.config:
            return self._loop.config.resolve_author(sender_id, channel)
        return sender_id

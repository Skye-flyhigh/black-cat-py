"""Message handler: processes a single inbound message."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from blackcat.agent.tools.message import MessageTool
from blackcat.bus.events import OutboundMessage
from blackcat.utils.helpers import truncate_string

if TYPE_CHECKING:
    from blackcat.agent.loop import AgentLoop
    from blackcat.bus.events import InboundMessage
    from blackcat.session.manager import Session


class MessageHandler:
    """
    Handles a single inbound message through the agent.

    Responsibilities:
    - Resolve message origin (system vs channel)
    - Get or create session
    - Build context messages
    - Run agent loop
    - Persist session
    - Construct response

    Not responsible for:
    - LLM iteration (delegates to AgentRunner)
    - Tool execution (delegates to ToolRegistry)
    - Bus consumption and task dispatch (AgentLoop)
    """

    __slots__ = ("_loop", "_msg")

    def __init__(self, loop: "AgentLoop", msg: "InboundMessage") -> None:
        self._loop = loop
        self._msg = msg

    async def process(
        self,
        session_key: str | None = None,
        on_progress=None,
        on_stream=None,
        on_stream_end=None,
    ) -> OutboundMessage | None:
        """
        Process the inbound message and return a response.

        Args:
            session_key: Optional session key override.
            on_progress: Optional progress callback.
            on_stream: Optional streaming callback.
            on_stream_end: Optional stream end callback.

        Returns:
            OutboundMessage to send, or None if no response needed.
        """
        loop = self._loop
        msg = self._msg

        # Parse origin (system messages encode origin in chat_id as "channel:chat_id")
        is_system = msg.channel == "system"
        if is_system:
            origin_channel, origin_chat_id = self._parse_system_origin(msg.chat_id)
            key = f"{origin_channel}:{origin_chat_id}"
            logger.info("Processing system message from {}", msg.sender_id)
        else:
            origin_channel, origin_chat_id = msg.channel, msg.chat_id
            key = session_key or msg.session_key
            logger.info(
                "Processing message from {}:{}: {}",
                origin_channel, msg.sender_id, truncate_string(msg.content, 80),
            )

        # Ensure MCP is connected (lazy)
        await loop._connect_mcp()

        # Get or create session
        session = loop.sessions.get_or_create(key)

        # Dispatch slash commands
        raw = msg.content.strip()
        from blackcat.command.router import CommandContext
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=loop)
        if result := await loop.commands.dispatch(ctx):
            return result

        # Set tool contexts and reset per-turn tracking
        loop._set_tool_context(origin_channel, origin_chat_id, msg.metadata.get("message_id"))
        message_tool = loop.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.start_turn()

        # Build context messages
        # Note: Don't limit get_history here - let sliding_window handle compaction
        author = self._resolve_author(msg.sender_id, msg.channel)
        messages = await loop.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            author=author,
            channel=origin_channel,
            chat_id=origin_chat_id,
            media=msg.media if msg.media and not is_system else None,
            use_structured_system=loop.provider.supports_prompt_caching,
        )

        # Compact context if needed
        messages, _ = await loop.context.sliding_window(
            messages,
            window_size=loop.memory_window,
            max_tokens=loop.context_window_tokens,
            model=loop.model,
            session=session,
        )

        # Progress callback for intermediate output
        async def _send_progress(content: str, **_kwargs) -> None:
            await loop.bus.publish_outbound(
                OutboundMessage(
                    channel=origin_channel,
                    chat_id=origin_chat_id,
                    content=content,
                    metadata=msg.metadata or {},
                )
            )

        # Run agent loop
        final_content, tools_used, _ = await loop._run_agent_loop(
            messages,
            on_progress=on_progress or _send_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            session=session,
            channel=origin_channel,
            chat_id=origin_chat_id,
            message_id=msg.metadata.get("message_id") if msg.metadata else None,
        )

        if final_content is None or not final_content.strip():
            from blackcat.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        # Log response
        logger.info(
            "Response to {}:{}: {}",
            origin_channel, msg.sender_id, truncate_string(final_content, 120),
        )

        # Persist to session
        self._save_to_session(session, msg, final_content, tools_used, is_system)

        # Avoid duplicate if message tool already sent
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return None

        metadata = msg.metadata or {}
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content,
            reply_to=str(metadata["reply_to"]) if metadata.get("reply_to") else None,
            metadata=metadata,
        )

    def _parse_system_origin(self, chat_id: str) -> tuple[str, str]:
        """Parse system message origin from chat_id."""
        from blackcat.utils.helpers import parse_session_key
        if ":" in chat_id:
            return parse_session_key(chat_id)
        return "cli", chat_id

    def _resolve_author(self, sender_id: str, channel: str) -> str:
        """Resolve sender_id to author name using config."""
        if self._loop.config:
            return self._loop.config.resolve_author(sender_id, channel)
        return sender_id

    def _save_to_session(
        self,
        session: "Session",
        msg: "InboundMessage",
        final_content: str,
        tools_used: list[dict[str, Any]],
        is_system: bool,
    ) -> None:
        """Persist conversation turn to session."""
        user_content = f"[System: {msg.sender_id}] {msg.content}" if is_system else msg.content
        author = self._resolve_author(msg.sender_id, msg.channel)
        session.add_message("user", user_content, author=author)

        agent = self._loop.context.get_identity()
        agent_name = agent.get("identity", {}).get("name") if isinstance(agent, dict) else None
        agent_name = agent_name or "blackcat"

        # Save assistant message with tool calls (for context continuity)
        if tools_used:
            tool_calls = [
                {"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": t["arguments"]}}
                for t in tools_used
            ]
            session.add_message("assistant", None, author=agent_name, tool_calls=tool_calls)
            for tool in tools_used:
                session.add_message(
                    "function",
                    content=tool["result"],
                    author=agent_name,
                    tool_call_id=tool["id"],
                    name=tool["name"],
                )
        session.add_message("assistant", final_content, author=agent_name)
        self._loop.sessions.save(session)

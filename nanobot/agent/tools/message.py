"""Message tool for sending messages to users."""

from typing import Any, Awaitable, Callable

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import OutboundMessage


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    name = "message"
    description = "Send a message to the user. Use this when you want to communicate something."
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The message content to send"},
            "channel": {
                "type": "string",
                "description": "Optional: target channel (telegram, discord, etc.)",
            },
            "chat_id": {"type": "string", "description": "Optional: target chat/user ID"},
        },
        "required": ["content"],
    }

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._sent_in_turn: bool = False

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the current message context."""
        self._default_channel = channel
        self._default_chat_id = chat_id

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking."""
        self._sent_in_turn = False

    async def execute(self, **kwargs: Any) -> str:
        content: str = kwargs["content"]
        channel: str | None = kwargs.get("channel")
        chat_id: str | None = kwargs.get("chat_id")
        channel = channel or self._default_channel
        chat_id = chat_id or self._default_chat_id

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        msg = OutboundMessage(channel=channel, chat_id=chat_id, content=content)

        try:
            await self._send_callback(msg)
            self._sent_in_turn = True
            return f"Message sent to {channel}:{chat_id}"
        except Exception as e:
            return f"Error sending message: {str(e)}"

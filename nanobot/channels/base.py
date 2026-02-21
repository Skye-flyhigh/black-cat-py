"""
Base channel class - the abstract interface all channels inherit from.

This module provides:
- BaseChannel: Abstract class with shared state and behavior
- Typing indicator management (start/stop/loop)
- Message validation and permission checking
- Media download helpers

For pure functions and constants, see utils.py.
"""

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.utils import MEDIA_DIR


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the nanobot message bus.

    Provides common functionality:
    - Message validation before sending
    - Typing indicator management (opt-in)
    - Media file download helpers
    - Permission checking
    """

    name: str = "base"

    # Subclasses can override this to enable typing indicators.
    # Set to 0 to disable, or a positive number for the interval in seconds.
    typing_interval: float = 0

    def __init__(self, config: Any, bus: MessageBus):
        """
        Initialize the channel.

        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
        """
        self.config = config
        self.bus = bus
        self._running = False
        self._typing_tasks: dict[str, asyncio.Task] = {}

    def _require_config(self, **fields: Any) -> bool:
        """Check that required config fields are set. Logs error and returns False if any missing."""
        for name, value in fields.items():
            if not value:
                logger.error("{} {} not configured", self.name, name)
                return False
        return True

    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.

        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass

    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.

        Validates the message before delegating to channel-specific implementation.
        Stops any typing indicator for this chat before sending.

        Args:
            msg: The message to send.
        """
        # Stop typing indicator before sending (or on empty)
        await self._stop_typing(msg.chat_id)

        if not msg.content or not msg.content.strip():
            logger.warning("Skipping empty message to {} on {}", msg.chat_id, self.name)
            return

        await self._send_impl(msg)

    @abstractmethod
    async def _send_impl(self, msg: OutboundMessage) -> None:
        """
        Channel-specific send implementation.

        Called by send() after validation. Subclasses must implement this.

        Args:
            msg: The validated message to send.
        """
        pass

    # ========================================================================
    # Typing Indicator Management
    # ========================================================================

    async def _start_typing(self, chat_id: str) -> None:
        """
        Start showing a typing indicator for a chat.

        Only works if typing_interval > 0 and _send_typing_indicator is implemented.
        """
        if self.typing_interval <= 0:
            return

        await self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    async def _stop_typing(self, chat_id: str) -> None:
        """Stop the typing indicator for a chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _typing_loop(self, chat_id: str) -> None:
        """Repeatedly send typing indicator until cancelled."""
        try:
            while self._running:
                await self._send_typing_indicator(chat_id)
                await asyncio.sleep(self.typing_interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Typing indicator stopped for {}: {}", chat_id, e)

    async def _send_typing_indicator(self, chat_id: str) -> None:
        """
        Send a single typing indicator to the platform.

        Subclasses should override this if they support typing indicators.
        """
        pass

    def _stop_all_typing(self) -> None:
        """Cancel all typing indicator tasks. Call this in stop()."""
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()

    # ========================================================================
    # Media Helpers
    # ========================================================================

    def _get_media_dir(self) -> Path:
        """Get the media directory, creating it if needed."""
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        return MEDIA_DIR

    async def _download_media(
        self,
        data: bytes,
        filename: str,
        file_id: str | None = None,
    ) -> Path:
        """
        Save media data to the media directory.

        Args:
            data: The file content as bytes.
            filename: Original filename (used for extension).
            file_id: Optional unique ID to prefix the filename.

        Returns:
            Path to the saved file.
        """
        media_dir = self._get_media_dir()

        # Sanitize filename
        safe_name = filename.replace("/", "_").replace("\\", "_")
        if file_id:
            safe_name = f"{file_id[:16]}_{safe_name}"

        file_path = media_dir / safe_name
        file_path.write_bytes(data)

        return file_path

    # ========================================================================
    # Permission Checking
    # ========================================================================

    def is_allowed(self, sender_id: str) -> bool:
        """
        Check if a sender is allowed to use this bot.

        Args:
            sender_id: The sender's identifier.

        Returns:
            True if allowed, False otherwise.
        """
        allow_list = getattr(self.config, "allow_from", [])

        # If no allow list, allow everyone
        if not allow_list:
            return True

        sender_str = str(sender_id)
        if sender_str in allow_list:
            return True

        # Support compound IDs like "12345|username"
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    return True

        return False

    # ========================================================================
    # Message Handling
    # ========================================================================

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Handle an incoming message from the chat platform.

        This method checks permissions and forwards to the bus.

        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media file paths.
            metadata: Optional channel-specific metadata.
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                f"Access denied for sender {sender_id} on channel {self.name}. "
                f"Add them to allowFrom list in config to grant access."
            )
            return

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
        )

        await self.bus.publish_inbound(msg)

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running

"""WhatsApp channel implementation using Node.js bridge."""

import asyncio
import json
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.utils import (
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
    format_reply_context,
)
from nanobot.config.schema import WhatsAppConfig


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel that connects to a Node.js bridge.

    The bridge uses @whiskeysockets/baileys to handle the WhatsApp Web protocol.
    Communication between Python and Node.js is via WebSocket.
    """

    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: WhatsAppConfig = config
        self._ws = None
        self._connected = False

    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets

        bridge_url = self.config.bridge_url

        logger.info("Connecting to WhatsApp bridge at {}...", bridge_url)

        self._running = True
        delay = RECONNECT_DELAY_INITIAL

        while self._running:
            try:
                extra_headers = {}
                if self.config.bridge_token:
                    extra_headers["Authorization"] = f"Bearer {self.config.bridge_token}"

                async with websockets.connect(bridge_url, additional_headers=extra_headers) as ws:
                    self._ws = ws
                    self._connected = True
                    delay = RECONNECT_DELAY_INITIAL  # reset on successful connection
                    logger.info("Connected to WhatsApp bridge")

                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            logger.error("Error handling bridge message: {}", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._ws = None
                if not self._running:
                    break
                logger.warning("WhatsApp bridge connection lost: {}", e)
                logger.info("Retrying in {}s...", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_DELAY_MAX)

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False

        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _send_impl(self, msg: OutboundMessage) -> None:
        """WhatsApp-specific send implementation."""
        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return

        try:
            payload = {
                "type": "send",
                "to": msg.chat_id,
                "text": msg.content,
            }
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error("Error sending WhatsApp message: {}", e)

    async def _handle_bridge_message(self, raw: str) -> None:
        """Handle a message from the bridge."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from bridge: {}", raw[:100])
            return

        msg_type = data.get("type")

        if msg_type == "message":
            await self._handle_incoming_message(data)
        elif msg_type == "status":
            status = data.get("status")
            logger.info("WhatsApp status: {}", status)
            self._connected = status == "connected"
        elif msg_type == "qr":
            logger.info("Scan QR code in the bridge terminal to connect WhatsApp")
        elif msg_type == "error":
            logger.error("WhatsApp bridge error: {}", data.get('error'))

    async def _handle_incoming_message(self, data: dict[str, Any]) -> None:
        """Handle an incoming WhatsApp message."""
        # Phone number (deprecated) or LID (new format)
        pn = data.get("pn", "")
        sender = data.get("sender", "")
        content = data.get("content", "")

        user_id = pn if pn else sender
        sender_id = user_id.split("@")[0] if "@" in user_id else user_id

        logger.debug("WhatsApp message from {}", sender_id)

        content_parts: list[str] = []

        # Include reply context if bridge provides it
        quoted = data.get("quoted") or data.get("quotedMessage")
        if quoted:
            reply_ctx = format_reply_context(
                author=data.get("quotedParticipant") or data.get("quotedAuthor"),
                content=quoted if isinstance(quoted, str) else quoted.get("text", ""),
            )
            if reply_ctx:
                content_parts.append(reply_ctx)

        # Handle voice messages
        if content == "[Voice Message]":
            logger.info("Voice message from {} - transcription not yet supported", sender_id)
            content = "[Voice Message: Transcription not available for WhatsApp yet]"

        content_parts.append(content)
        final_content = "\n".join(content_parts)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=sender,  # Use full LID for replies
            content=final_content,
            metadata={
                "message_id": data.get("id"),
                "timestamp": data.get("timestamp"),
                "is_group": data.get("isGroup", False),
            },
        )

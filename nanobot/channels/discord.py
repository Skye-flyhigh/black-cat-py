"""Discord channel implementation using Discord Gateway websocket."""

import asyncio
import json
from typing import Any

import httpx
import websockets
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.utils import (
    MAX_ATTACHMENT_BYTES,
    RECONNECT_DELAY_SECONDS,
    TYPING_INTERVAL_DISCORD,
    format_reply_context,
)
from nanobot.config.schema import DiscordConfig

DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordChannel(BaseChannel):
    """Discord channel using Gateway websocket."""

    name = "discord"
    typing_interval = TYPING_INTERVAL_DISCORD

    def __init__(self, config: DiscordConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: DiscordConfig = config
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._seq: int | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Start the Discord gateway connection."""
        if not self.config.token:
            logger.error("Discord bot token not configured")
            return

        self._running = True
        self._http = httpx.AsyncClient(timeout=30.0)

        while self._running:
            try:
                logger.info("Connecting to Discord gateway...")
                async with websockets.connect(self.config.gateway_url) as ws:
                    self._ws = ws
                    await self._gateway_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Discord gateway error: {e}")
                if self._running:
                    logger.info(f"Reconnecting to Discord gateway in {RECONNECT_DELAY_SECONDS} seconds...")
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def stop(self) -> None:
        """Stop the Discord channel."""
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        self._stop_all_typing()

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._http:
            await self._http.aclose()
            self._http = None

    async def _send_impl(self, msg: OutboundMessage) -> None:
        """Discord-specific send implementation."""
        if not self._http:
            logger.warning("Discord HTTP client not initialized")
            return

        url = f"{DISCORD_API_BASE}/channels/{msg.chat_id}/messages"
        payload: dict[str, Any] = {"content": msg.content}

        if msg.reply_to:
            payload["message_reference"] = {"message_id": msg.reply_to}
            payload["allowed_mentions"] = {"replied_user": False}

        headers = {"Authorization": f"Bot {self.config.token}"}

        for attempt in range(3):
            try:
                response = await self._http.post(url, headers=headers, json=payload)
                if response.status_code == 429:
                    data = response.json()
                    retry_after = float(data.get("retry_after", 1.0))
                    logger.warning(f"Discord rate limited, retrying in {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                return
            except Exception as e:
                if attempt == 2:
                    logger.error(f"Error sending Discord message: {e}")
                else:
                    await asyncio.sleep(1)

    async def _send_typing_indicator(self, chat_id: str) -> None:
        """Send typing indicator to Discord."""
        if not self._http:
            return

        url = f"{DISCORD_API_BASE}/channels/{chat_id}/typing"
        headers = {"Authorization": f"Bot {self.config.token}"}

        try:
            await self._http.post(url, headers=headers)
        except Exception:
            pass  # Typing indicator failure is not critical

    # ========================================================================
    # Gateway Protocol
    # ========================================================================

    async def _gateway_loop(self) -> None:
        """Main gateway loop: identify, heartbeat, dispatch events."""
        if not self._ws:
            return

        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from Discord gateway: {raw[:100]}")
                continue

            op = data.get("op")
            event_type = data.get("t")
            seq = data.get("s")
            payload = data.get("d")

            if seq is not None:
                self._seq = seq

            if op == 10:
                # HELLO: start heartbeat and identify
                interval_ms = payload.get("heartbeat_interval", 45000)
                await self._start_heartbeat(interval_ms / 1000)
                await self._identify()
            elif op == 0 and event_type == "READY":
                logger.info("Discord gateway READY")
            elif op == 0 and event_type == "MESSAGE_CREATE":
                await self._handle_message_create(payload)
            elif op == 7:
                # RECONNECT
                logger.info("Discord gateway requested reconnect")
                break
            elif op == 9:
                # INVALID_SESSION
                logger.warning("Discord gateway invalid session")
                break

    async def _identify(self) -> None:
        """Send IDENTIFY payload."""
        if not self._ws:
            return

        identify = {
            "op": 2,
            "d": {
                "token": self.config.token,
                "intents": self.config.intents,
                "properties": {
                    "os": "nanobot",
                    "browser": "nanobot",
                    "device": "nanobot",
                },
            },
        }
        await self._ws.send(json.dumps(identify))

    async def _start_heartbeat(self, interval_s: float) -> None:
        """Start or restart the heartbeat loop."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        async def heartbeat_loop() -> None:
            while self._running and self._ws:
                payload = {"op": 1, "d": self._seq}
                try:
                    await self._ws.send(json.dumps(payload))
                except Exception as e:
                    logger.warning(f"Discord heartbeat failed: {e}")
                    break
                await asyncio.sleep(interval_s)

        self._heartbeat_task = asyncio.create_task(heartbeat_loop())

    # ========================================================================
    # Message Handling
    # ========================================================================

    async def _handle_message_create(self, payload: dict[str, Any]) -> None:
        """Handle incoming Discord messages."""
        author = payload.get("author") or {}
        if author.get("bot"):
            return

        sender_id = str(author.get("id", ""))
        channel_id = str(payload.get("channel_id", ""))
        content = payload.get("content") or ""

        if not sender_id or not channel_id:
            return

        if not self.is_allowed(sender_id):
            return

        content_parts: list[str] = []
        media_paths: list[str] = []

        # Include referenced message content for context (replies)
        ref_msg = payload.get("referenced_message")
        if ref_msg:
            reply_ctx = format_reply_context(
                author=(ref_msg.get("author") or {}).get("username"),
                content=ref_msg.get("content", ""),
            )
            if reply_ctx:
                content_parts.append(reply_ctx)

        # Add the actual message content
        if content:
            content_parts.append(content)

        # Process attachments
        for attachment in payload.get("attachments") or []:
            await self._process_attachment(attachment, content_parts, media_paths)

        reply_to = (ref_msg or {}).get("id")

        await self._start_typing(channel_id)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=channel_id,
            content="\n".join(p for p in content_parts if p) or "[empty message]",
            media=media_paths,
            metadata={
                "message_id": str(payload.get("id", "")),
                "guild_id": payload.get("guild_id"),
                "reply_to": reply_to,
            },
        )

    async def _process_attachment(
        self,
        attachment: dict[str, Any],
        content_parts: list[str],
        media_paths: list[str],
    ) -> None:
        """Process a single Discord attachment."""
        url = attachment.get("url")
        filename = attachment.get("filename") or "attachment"
        size = attachment.get("size") or 0
        file_id = attachment.get("id", "file")

        if not url or not self._http:
            return

        if size and size > MAX_ATTACHMENT_BYTES:
            content_parts.append(f"[attachment: {filename} - too large]")
            return

        try:
            resp = await self._http.get(url)
            resp.raise_for_status()

            file_path = await self._download_media(
                data=resp.content,
                filename=filename,
                file_id=file_id,
            )

            media_paths.append(str(file_path))
            content_parts.append(f"[attachment: {file_path}]")

        except Exception as e:
            logger.warning(f"Failed to download Discord attachment: {e}")
            content_parts.append(f"[attachment: {filename} - download failed]")

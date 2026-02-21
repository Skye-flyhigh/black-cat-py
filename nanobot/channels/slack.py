"""Slack channel implementation using Socket Mode."""

import asyncio
import re

from loguru import logger
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import SlackConfig


class SlackChannel(BaseChannel):
    """Slack channel using Socket Mode."""

    name = "slack"

    def __init__(self, config: SlackConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: SlackConfig = config
        self._web_client: AsyncWebClient | None = None
        self._socket_client: SocketModeClient | None = None
        self._bot_user_id: str | None = None

    async def start(self) -> None:
        """Start the Slack Socket Mode client."""
        if not self.config.bot_token or not self.config.app_token:
            logger.error("Slack bot/app token not configured")
            return

        if self.config.mode != "socket":
            logger.error(f"Unsupported Slack mode: {self.config.mode}")
            return

        self._running = True

        self._web_client = AsyncWebClient(token=self.config.bot_token)
        self._socket_client = SocketModeClient(
            app_token=self.config.app_token,
            web_client=self._web_client,
        )

        self._socket_client.socket_mode_request_listeners.append(self._on_socket_request)

        # Resolve bot user ID for mention handling
        try:
            auth = await self._web_client.auth_test()
            self._bot_user_id = auth.get("user_id")
            logger.info(f"Slack bot connected as {self._bot_user_id}")
        except Exception as e:
            logger.warning(f"Slack auth_test failed: {e}")

        logger.info("Starting Slack Socket Mode client...")
        await self._socket_client.connect()

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Slack client."""
        self._running = False

        if self._socket_client:
            try:
                await self._socket_client.close()
            except Exception as e:
                logger.warning(f"Slack socket close failed: {e}")
            self._socket_client = None

    async def _send_impl(self, msg: OutboundMessage) -> None:
        """Slack-specific send implementation."""
        if not self._web_client:
            logger.warning("Slack client not running")
            return

        try:
            slack_meta = msg.metadata.get("slack", {}) if msg.metadata else {}
            thread_ts = slack_meta.get("thread_ts")
            channel_type = slack_meta.get("channel_type")

            # Only reply in thread for channel/group messages; DMs don't use threads
            use_thread = thread_ts and channel_type != "im"

            await self._web_client.chat_postMessage(
                channel=msg.chat_id,
                text=msg.content,
                thread_ts=thread_ts if use_thread else None,
            )
        except Exception as e:
            logger.error(f"Error sending Slack message: {e}")

    # ========================================================================
    # Event Handling
    # ========================================================================

    async def _on_socket_request(
        self,
        client: SocketModeClient,
        req: SocketModeRequest,
    ) -> None:
        """Handle incoming Socket Mode requests."""
        if req.type != "events_api":
            return

        # Acknowledge immediately
        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        payload = req.payload or {}
        event = payload.get("event") or {}
        event_type = event.get("type")

        if event_type not in ("message", "app_mention"):
            return

        sender_id = event.get("user")
        chat_id = event.get("channel")

        # Ignore bot/system messages
        if event.get("subtype"):
            return
        if self._bot_user_id and sender_id == self._bot_user_id:
            return

        text = event.get("text") or ""

        # Avoid double-processing: Slack sends both `message` and `app_mention`
        if event_type == "message" and self._bot_user_id and f"<@{self._bot_user_id}>" in text:
            return

        logger.debug(
            f"Slack event: type={event_type} user={sender_id} channel={chat_id} text={text[:80]}"
        )

        if not sender_id or not chat_id:
            return

        channel_type = event.get("channel_type") or ""

        if not self._is_allowed(sender_id, chat_id, channel_type):
            return

        if channel_type != "im" and not self._should_respond(event_type, text, chat_id):
            return

        text = self._strip_bot_mention(text)
        thread_ts = event.get("thread_ts") or event.get("ts")

        # Add :eyes: reaction (best-effort)
        await self._add_reaction(chat_id, event.get("ts"), "eyes")

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=text,
            metadata={
                "slack": {
                    "event": event,
                    "thread_ts": thread_ts,
                    "channel_type": channel_type,
                }
            },
        )

    async def _add_reaction(self, channel: str, timestamp: str | None, emoji: str) -> None:
        """Add a reaction to a message."""
        if not self._web_client or not timestamp:
            return

        try:
            await self._web_client.reactions_add(
                channel=channel,
                name=emoji,
                timestamp=timestamp,
            )
        except Exception as e:
            logger.debug(f"Slack reactions_add failed: {e}")

    # ========================================================================
    # Policy Checks
    # ========================================================================

    def _is_allowed(self, sender_id: str, chat_id: str, channel_type: str) -> bool:
        """Check if sender/channel is allowed based on config."""
        if channel_type == "im":
            if not self.config.dm.enabled:
                return False
            if self.config.dm.policy == "allowlist":
                return sender_id in self.config.dm.allow_from
            return True

        # Group/channel messages
        if self.config.group_policy == "allowlist":
            return chat_id in self.config.group_allow_from
        return True

    def _should_respond(self, event_type: str, text: str, chat_id: str) -> bool:
        """Check if bot should respond in a channel based on policy."""
        if self.config.group_policy == "open":
            return True
        if self.config.group_policy == "mention":
            if event_type == "app_mention":
                return True
            return self._bot_user_id is not None and f"<@{self._bot_user_id}>" in text
        if self.config.group_policy == "allowlist":
            return chat_id in self.config.group_allow_from
        return False

    def _strip_bot_mention(self, text: str) -> str:
        """Remove bot mention from message text."""
        if not text or not self._bot_user_id:
            return text
        return re.sub(rf"<@{re.escape(self._bot_user_id)}>\s*", "", text).strip()

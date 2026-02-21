"""Telegram channel implementation using python-telegram-bot."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger
from telegram import BotCommand, Message, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.utils import (
    MAX_MESSAGE_LENGTH_TELEGRAM,
    TYPING_INTERVAL_TELEGRAM,
    format_reply_context,
    get_file_extension,
    markdown_to_telegram_html,
    split_message,
)
from nanobot.config.schema import TelegramConfig

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


class TelegramChannel(BaseChannel):
    """
    Telegram channel using long polling.

    Simple and reliable - no webhook/public IP needed.
    """

    name = "telegram"
    typing_interval = TYPING_INTERVAL_TELEGRAM

    # Commands registered with Telegram's command menu
    BOT_COMMANDS = [
        BotCommand("start", "Start the bot"),
        BotCommand("reset", "Reset conversation history"),
        BotCommand("help", "Show available commands"),
    ]

    def __init__(
        self,
        config: TelegramConfig,
        bus: MessageBus,
        groq_api_key: str = "",
        session_manager: SessionManager | None = None,
    ):
        super().__init__(config, bus)
        self.config: TelegramConfig = config
        self.groq_api_key = groq_api_key
        self.session_manager = session_manager
        self._app: Application | None = None

    async def start(self) -> None:
        """Start the Telegram bot with long polling."""
        if not self.config.token:
            logger.error("Telegram bot token not configured")
            return

        self._running = True

        # Build the application with a larger connection pool for stability
        from telegram.request import HTTPXRequest

        request = HTTPXRequest(connection_pool_size=16, connect_timeout=20.0)
        builder = Application.builder().token(self.config.token).request(request)
        if self.config.proxy:
            builder = builder.proxy(self.config.proxy).get_updates_proxy(self.config.proxy)
        self._app = builder.build()

        # Add handlers
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("reset", self._on_reset))
        self._app.add_handler(CommandHandler("help", self._on_help))
        self._app.add_handler(
            MessageHandler(
                (
                    filters.TEXT
                    | filters.PHOTO
                    | filters.VOICE
                    | filters.AUDIO
                    | filters.Document.ALL
                )
                & ~filters.COMMAND,
                self._on_message,
            )
        )

        logger.info("Starting Telegram bot (polling mode)...")

        await self._app.initialize()
        await self._app.start()

        bot_info = await self._app.bot.get_me()
        logger.info(f"Telegram bot @{bot_info.username} connected")

        try:
            await self._app.bot.set_my_commands(self.BOT_COMMANDS)
            logger.debug("Telegram bot commands registered")
        except Exception as e:
            logger.warning(f"Failed to register bot commands: {e}")

        if self._app.updater:
            await self._app.updater.start_polling(
                allowed_updates=["message"],
                drop_pending_updates=True,
            )

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        self._running = False
        self._stop_all_typing()

        if self._app:
            logger.info("Stopping Telegram bot...")
            if self._app.updater:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    async def _send_impl(self, msg: OutboundMessage) -> None:
        """Telegram-specific send implementation."""
        if not self._app:
            logger.warning("Telegram bot not running")
            return

        try:
            chat_id = int(msg.chat_id)
        except ValueError:
            logger.error(f"Invalid chat_id: {msg.chat_id}")
            return

        # Reply-to support (configurable)
        reply_params: dict = {}
        if self.config.reply_to_message and msg.reply_to:
            reply_params["reply_to_message_id"] = int(msg.reply_to)

        # Split long messages to stay within Telegram's 4096-char limit
        chunks = split_message(msg.content, MAX_MESSAGE_LENGTH_TELEGRAM)
        for chunk in chunks:
            html_content = markdown_to_telegram_html(chunk)
            try:
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=html_content,
                    parse_mode="HTML",
                    **reply_params,
                )
            except Exception as e:
                # Fallback to plain text if HTML parsing fails
                logger.warning(f"HTML parse failed, falling back to plain text: {e}")
                try:
                    await self._app.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        **reply_params,
                    )
                except Exception as e2:
                    logger.error(f"Error sending Telegram message: {e2}")
            # Only reply-to the first chunk
            reply_params = {}

    async def _send_typing_indicator(self, chat_id: str) -> None:
        """Send typing indicator to Telegram."""
        if self._app:
            await self._app.bot.send_chat_action(chat_id=int(chat_id), action="typing")

    # ========================================================================
    # Command Handlers
    # ========================================================================

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.message or not update.effective_user:
            return

        user = update.effective_user
        await update.message.reply_text(
            f"Hi {user.first_name}! I'm here.\n\n"
            "Send me a message and I'll respond.\n"
            "Type /help to see available commands."
        )

    async def _on_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /reset command - clear conversation history."""
        if not update.message or not update.effective_user:
            return

        chat_id = str(update.message.chat_id)
        session_key = f"{self.name}:{chat_id}"

        if self.session_manager is None:
            logger.warning("/reset called but session_manager is not available")
            await update.message.reply_text("Session management is not available.")
            return

        session = self.session_manager.get_or_create(session_key)
        msg_count = len(session.messages)
        session.clear()
        self.session_manager.save(session)

        logger.info(f"Session reset for {session_key} (cleared {msg_count} messages)")
        await update.message.reply_text("Conversation history cleared. Let's start fresh!")

    async def _on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        if not update.message:
            return

        help_text = (
            "<b>Commands</b>\n\n"
            "/start — Start the bot\n"
            "/reset — Reset conversation history\n"
            "/help — Show this help message\n\n"
            "Just send me a text message to chat!"
        )
        await update.message.reply_text(help_text, parse_mode="HTML")

    # ========================================================================
    # Message Handler
    # ========================================================================

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming messages (text, photos, voice, documents)."""
        if not update.message or not update.effective_user:
            return

        message = update.message
        user = update.effective_user
        chat_id = str(message.chat_id)

        # Build sender ID (numeric + optional username for allowlist compatibility)
        sender_id = str(user.id)
        if user.username:
            sender_id = f"{sender_id}|{user.username}"

        # Collect content and media
        content_parts: list[str] = []
        media_paths: list[str] = []

        # Include reply context if this is a reply to another message
        if message.reply_to_message:
            ref = message.reply_to_message
            ref_author = ref.from_user.first_name if ref.from_user else None
            ref_content = ref.text or ref.caption or ""
            reply_ctx = format_reply_context(author=ref_author, content=ref_content)
            if reply_ctx:
                content_parts.append(reply_ctx)

        if message.text:
            content_parts.append(message.text)
        if message.caption:
            content_parts.append(message.caption)

        # Handle media
        await self._process_media(message, content_parts, media_paths)

        content = "\n".join(content_parts) if content_parts else "[empty message]"

        logger.debug(f"Telegram message from {sender_id}: {content[:50]}...")

        # Start typing indicator
        await self._start_typing(chat_id)

        # Forward to message bus
        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            media=media_paths,
            metadata={
                "message_id": message.message_id,
                "reply_to": message.message_id if self.config.reply_to_message else None,
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "is_group": message.chat.type != "private",
            },
        )

    async def _process_media(
        self,
        message: Message,
        content_parts: list[str],
        media_paths: list[str],
    ) -> None:
        """Process media attachments from a message."""
        media_file = None
        media_type = None

        if message.photo:
            media_file = message.photo[-1]  # Largest photo
            media_type = "image"
        elif message.voice:
            media_file = message.voice
            media_type = "voice"
        elif message.audio:
            media_file = message.audio
            media_type = "audio"
        elif message.document:
            media_file = message.document
            media_type = "file"

        if not media_file or not media_type or not self._app:
            return

        try:
            file = await self._app.bot.get_file(media_file.file_id)
            ext = get_file_extension(media_type, getattr(media_file, "mime_type", None))
            filename = f"{media_file.file_id[:16]}{ext}"

            file_path = self._get_media_dir() / filename
            await file.download_to_drive(str(file_path))
            media_paths.append(str(file_path))

            # Handle voice/audio transcription
            if media_type in ("voice", "audio"):
                transcription = await self._transcribe_audio(file_path)
                if transcription:
                    content_parts.append(f"[transcription: {transcription}]")
                else:
                    content_parts.append(f"[{media_type}: {file_path}]")
            else:
                content_parts.append(f"[{media_type}: {file_path}]")

            logger.debug(f"Downloaded {media_type} to {file_path}")

        except Exception as e:
            logger.error(f"Failed to download media: {e}")
            content_parts.append(f"[{media_type}: download failed]")

    async def _transcribe_audio(self, file_path) -> str | None:
        """Transcribe audio file using Groq."""
        if not self.groq_api_key:
            return None

        try:
            from nanobot.providers.transcription import GroqTranscriptionProvider

            transcriber = GroqTranscriptionProvider(api_key=self.groq_api_key)
            transcription = await transcriber.transcribe(file_path)
            if transcription:
                logger.info(f"Transcribed audio: {transcription[:50]}...")
            return transcription
        except Exception as e:
            logger.warning(f"Transcription failed: {e}")
            return None

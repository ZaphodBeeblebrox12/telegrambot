""" Telegram Bot Integration - Fully Config-Driven with Outbox Pattern + Rate Limiting
Receives messages, processes through orchestrator. Uses python-telegram-bot v20+ (async)
Pipeline: CONFIG → ROUTER → EXECUTOR → SERVICE → FORMATTER → MAPPING → OUTBOX → PUBLISHER
FIXED: Rate limiting now at TOP of handler, preserves exact original flow.
FIXED: Safe message access (effective_message) for channel posts.
FIXED: Outbox handler registered.
FIXED: Admin caption editing, image forwarding, target message ID tracking, reply threading.
"""

import logging
import os
import asyncio
import uuid
from pathlib import Path
from typing import Optional

from telegram import Update, ReplyParameters
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config.config_loader import config
from orchestration.orchestrator import get_orchestrator
from messaging.message_mapping_service import get_mapping_service
from core.repositories import RepositoryFactory
from core.rate_limit_manager import get_rate_limit_manager
from core.outbox import get_outbox

logger = logging.getLogger(__name__)

TEMP_IMAGE_DIR = Path("temp_images")
TEMP_IMAGE_DIR.mkdir(exist_ok=True)

class TradingBot:
    """Telegram bot for trading signal processing - fully config-driven with outbox."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")

        self.cfg = config
        self.orchestrator = get_orchestrator()
        self.mapping_service = get_mapping_service()
        self.trade_repo = RepositoryFactory.get_trade_repository()
        self.rate_limiter = get_rate_limit_manager()

        # Build application
        self.application = Application.builder().token(self.token).build()
        self._setup_handlers()
        self._setup_outbox_handler()

        logger.info("TradingBot initialized (config-driven v2.0 with outbox + rate limiting + image flow)")

    def _setup_handlers(self):
        """Setup message handlers – also handle channel posts."""
        # Command handlers
        self.application.add_handler(CommandHandler("start", self._cmd_start))
        self.application.add_handler(CommandHandler("help", self._cmd_help))
        self.application.add_handler(CommandHandler("status", self._cmd_status))

        # Photo handler – works for private chats, groups, AND channels
        self.application.add_handler(
            MessageHandler(filters.PHOTO, self._handle_image)
        )

        # Text handler – works for private chats, groups, AND channels
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

        self.application.add_error_handler(self._handle_error)

    def _setup_outbox_handler(self):
        """Register a real Telegram handler with the outbox manager."""
        outbox = get_outbox()
        outbox.register_handler("telegram", self._outbox_telegram_handler)

    async def _outbox_telegram_handler(self, payload: dict):
        """Outbox handler that actually sends messages and photos via the bot.

        CRITICAL FIX: Now handles photo_path for image forwarding and stores
        target message IDs for reply threading using trade_id.
        """
        channel_id = payload.get("channel_id")
        text = payload.get("text")
        reply_to = payload.get("reply_to_message_id")
        photo_path = payload.get("photo_path")
        trade_id = payload.get("trade_id")

        if not channel_id or not text:
            logger.error("Outbox payload missing channel_id or text")
            return

        try:
            if photo_path and os.path.exists(photo_path):
                with open(photo_path, 'rb') as photo_file:
                    sent = await self.application.bot.send_photo(
                        chat_id=channel_id,
                        photo=photo_file,
                        caption=text,
                        reply_to_message_id=reply_to,
                        parse_mode="HTML",
                    )
            else:
                sent = await self.application.bot.send_message(
                    chat_id=channel_id,
                    text=text,
                    reply_to_message_id=reply_to,
                    parse_mode="HTML",
                )

            # CRITICAL FIX: Store target message ID for reply threading using trade_id
            if trade_id and sent and hasattr(sent, 'message_id'):
                try:
                    self.mapping_service.add_target_message(trade_id, channel_id, sent.message_id)
                except Exception as e:
                    logger.warning(f"Failed to store target message ID: {e}")

        except Exception as e:
            error_str = str(e).lower()
            # Fallback to plain text if HTML parsing fails
            if "can't parse" in error_str or "html" in error_str:
                logger.warning(f"HTML parse failed, retrying as plain text: {e}")
                try:
                    if photo_path and os.path.exists(photo_path):
                        with open(photo_path, 'rb') as photo_file:
                            sent = await self.application.bot.send_photo(
                                chat_id=channel_id,
                                photo=photo_file,
                                caption=text,
                                reply_to_message_id=reply_to,
                                parse_mode=None,
                            )
                    else:
                        sent = await self.application.bot.send_message(
                            chat_id=channel_id,
                            text=text,
                            reply_to_message_id=reply_to,
                            parse_mode=None,
                        )

                    if trade_id and sent and hasattr(sent, 'message_id'):
                        try:
                            self.mapping_service.add_target_message(trade_id, channel_id, sent.message_id)
                        except Exception as e2:
                            logger.warning(f"Failed to store target message ID on fallback: {e2}")
                    return
                except Exception as e2:
                    logger.exception(f"Plain text fallback also failed: {e2}")
                    raise
            logger.exception(f"Outbox send failed: {e}")
            raise

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        msg = update.effective_message
        if not msg:
            return
        await msg.reply_text(
            "🚀 Trading Bot Ready (Config-Driven v2.0 + Outbox)\n\n"
            "Send me a chart image to create a trade setup\n"
            "Reply to trade messages with update commands.\n\n"
            "Available: trail, closed, target, stopped,\n"
            "breakeven, partial, closehalf, pyramid, note, cancel"
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        msg = update.effective_message
        if not msg:
            return
        handlers = self.orchestrator.executor.list_handlers()
        await msg.reply_text(
            f"📚 Config-Driven Trading Bot v{config.system.version}\n\n"
            f"Handlers: {', '.join(handlers[:8])}...\n\n"
            "Reply to any trade message with:\n"
            "• trail - Update trailing stop\n"
            "• closed - Close trade\n"
            "• target - Target hit\n"
            "• stopped - Stopped out\n"
            "• breakeven - Close at breakeven\n"
            "• partial [%] - Partial close (FIFO)\n"
            "• closehalf - Close 50% (FIFO)\n"
            "• pyramid [%] - Add to position\n"
            "• note - Add note\n"
            "• cancel [reason] - Cancel trade"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        msg = update.effective_message
        if not msg:
            return
        status = self.orchestrator.get_system_status()
        await msg.reply_text(
            f"📊 System Status\n"
            f"Version: {status['config_version']}\n"
            f"Handlers: {', '.join(status['handlers_registered'])}\n"
            f"Open Trades: {status['trade_stats']['open_trades']}"
        )

    async def _handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming chart images.

        CRITICAL FIXES:
        1. Saves image to temp file for forwarding pipeline
        2. Edits admin message caption with formatted signal (old bot behavior)
        3. Routes image+command to update handler with photo_path
        """
        msg = update.effective_message
        if not msg:
            return
        if not msg.photo:
            await msg.reply_text("❌ No photo found")
            return

        # Rate limiting guard
        if not self.rate_limiter.allow_global_send():
            await msg.reply_text("⏳ Global rate limit reached. Please wait.")
            return

        # Download the largest photo
        photo_file = await msg.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()

        # CRITICAL FIX: Save image to temp file for forwarding pipeline
        photo_path = None
        try:
            photo_path = TEMP_IMAGE_DIR / f"{msg.message_id}_{uuid.uuid4().hex[:8]}.jpg"
            with open(photo_path, 'wb') as f:
                f.write(image_bytes)
            logger.info(f"Saved temp image: {photo_path}")
        except Exception as e:
            logger.warning(f"Failed to save temp image: {e}")
            photo_path = None

        # Check if this is an update command on an image (e.g., image with /update caption)
        caption = msg.caption or ""
        is_update_command = (
            msg.reply_to_message is not None or 
            '/update' in caption.lower() or
            'update ' in caption.lower() or
            'trail ' in caption.lower() or
            'closed ' in caption.lower() or
            'target ' in caption.lower() or
            'stopped ' in caption.lower() or
            'breakeven' in caption.lower() or
            'partial ' in caption.lower() or
            'closehalf ' in caption.lower() or
            'cancel' in caption.lower() or
            'note ' in caption.lower() or
            'newtarget ' in caption.lower() or
            'stop ' in caption.lower() or
            'pyramid ' in caption.lower()
        )

        if is_update_command:
            logger.info(f"Update command detected in image caption. Routing with photo_path.")
            # Route to text handler with photo path for forwarding
            await self._handle_text(update, context, photo_path=str(photo_path) if photo_path else None)
            return

        try:
            # Process trade setup
            result = await self.orchestrator.process_image(
                image_bytes=bytes(image_bytes),
                admin_channel_id=msg.chat_id,
                message_id=msg.message_id,
                photo_path=str(photo_path) if photo_path else None
            )

            if not result.get("success"):
                errors = result.get("errors", [])
                error = errors[0] if errors else "Unknown error"
                await msg.reply_text(f"❌ OCR failed: {error}")
                return

            # CRITICAL FIX: Edit admin message caption with formatted trade signal
            formatted_text = result.get("formatted_text")
            if formatted_text:
                try:
                    await context.bot.edit_message_caption(
                        chat_id=msg.chat_id,
                        message_id=msg.message_id,
                        caption=formatted_text,
                        parse_mode="HTML"
                    )
                    logger.info(f"Edited admin message {msg.message_id} caption with trade setup")
                except Exception as e:
                    logger.warning(f"Could not edit admin caption: {e}")

            self.rate_limiter.record_global_send()

            trade = result.get("trade")
            if trade:
                await msg.reply_text(
                    f"✅ Trade created: {trade.symbol} {trade.side}\n"
                    f"Entry: {trade.entry_price} | Target: {trade.target} | Stop: {trade.stop_loss}"
                )
                logger.info(f"Trade created from image: {trade.symbol}")

        except Exception as e:
            logger.exception("Error processing image")
            await msg.reply_text(f"❌ Error: {str(e)}")

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE, photo_path: str = None):
        """Handle incoming text messages (commands).

        FIX: Accepts optional photo_path for image+command forwarding.
        """
        msg = update.effective_message
        if not msg:
            return
        text = msg.text.strip() if msg.text else ""

        # If routed from image handler, use caption as text
        if not text and msg.caption:
            text = msg.caption.strip()

        reply_to = msg.reply_to_message

        if not reply_to:
            await msg.reply_text(
                "ℹ️ Please reply to a trade message with a command.\n"
                "Or send an image to create a new trade."
            )
            return

        await self._process_command(
            update, context, command_text=text, reply_to_msg_id=reply_to.message_id, photo_path=photo_path
        )

    async def _process_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        command_text: str,
        reply_to_msg_id: int,
        photo_path: str = None,
    ):
        """Process command through orchestrator with outbox and rate limiting.

        CRITICAL FIXES:
        1. Accepts photo_path for image+command forwarding
        2. Edits image caption for image updates (old bot behavior)
        3. Properly handles result from orchestrator
        """
        msg = update.effective_message
        if not msg:
            return

        # RESOLVE TRADE_ID FIRST
        mapping = self.mapping_service.get_mapping(reply_to_msg_id)
        if not mapping:
            await msg.reply_text("❌ No trade found for this message")
            return
        trade_id = mapping.trade_id

        # RATE LIMIT GUARDS (TOP OF HANDLER)
        # 1. Check for exact duplicate
        if self.rate_limiter.is_duplicate(command_text, trade_id):
            logger.info(f"Duplicate command blocked: {command_text[:20]}... for trade {trade_id}")
            await msg.reply_text("⚠️ Duplicate command detected. Ignored.")
            return

        # 2. Check per-trade-command cooldown
        if not self.rate_limiter.allow_trade_update(trade_id, command_text):
            cooldown = self.rate_limiter.get_cooldown_remaining(trade_id, command_text)
            logger.info(f"Rate limit hit for trade {trade_id}: {cooldown:.1f}s remaining")
            await msg.reply_text(f"⏳ Please wait {cooldown:.1f}s before repeating this command")
            return

        # 3. Check global rate limit
        if not self.rate_limiter.allow_global_send():
            logger.warning("Global rate limit hit")
            await msg.reply_text("⏳ Global rate limit reached. Please wait.")
            return

        # 4. Acquire lock to prevent concurrent updates to same trade
        if not self.rate_limiter.acquire_update_lock(trade_id):
            await msg.reply_text("⏳ Update already in progress. Please wait.")
            return

        try:
            result = await self.orchestrator.process_command(
                command_text=command_text,
                reply_to_message_id=reply_to_msg_id,
                admin_channel_id=msg.chat_id,
                photo_path=photo_path,
                is_image_update=msg.photo is not None
            )

            if not result or not result.get("success"):
                error_msg = result["errors"][0] if result and result.get("errors") else "Command failed"
                await msg.reply_text(f"❌ {error_msg}")
                return

            # CRITICAL FIX: For image updates, edit the caption instead of sending text reply
            if msg.photo and result.get("formatted_text"):
                try:
                    await context.bot.edit_message_caption(
                        chat_id=msg.chat_id,
                        message_id=msg.message_id,
                        caption=result["formatted_text"],
                        parse_mode="HTML"
                    )
                    logger.info(f"Edited update image caption for msg {msg.message_id}")
                except Exception as e:
                    logger.warning(f"Could not edit update image caption: {e}")
            else:
                # For text-only updates, outbox already sent admin reply.
                # Do NOT send duplicate confirmation.
                pass

            self.rate_limiter.record_trade_update(trade_id, command_text)
            self.rate_limiter.record_global_send()

            # Delete command if configured (ONLY for text commands, preserve image updates)
            cmd_config = config.commands.get("/update")
            if cmd_config and cmd_config.delete_command and not msg.photo:
                try:
                    await msg.delete()
                except Exception:
                    pass

            logger.info(f"Command processed: {command_text}")

        except Exception as e:
            logger.exception("Error processing command")
            await msg.reply_text(f"❌ Error: {str(e)}")
        finally:
            # RELEASE LOCK (always)
            self.rate_limiter.release_update_lock(trade_id)

    async def _handle_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors."""
        logger.error(f"Update {update} caused error {context.error}")
        msg = update.effective_message if update else None
        if msg:
            await msg.reply_text("❌ An error occurred. Please try again.")

    async def post_init(self, application: Application):
        """Post-initialization hook - start outbox processor."""
        logger.info("Starting outbox processor...")
        asyncio.create_task(self.orchestrator.start_outbox_processor(interval=5.0))

    async def post_shutdown(self, application: Application):
        """Post-shutdown hook - stop outbox processor."""
        logger.info("Stopping outbox processor...")
        self.orchestrator.stop_outbox_processor()

    def run(self):
        """Start the bot (blocking)."""
        logger.info("Starting Telegram bot polling...")
        self.application.post_init = self.post_init
        self.application.post_shutdown = self.post_shutdown
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

    async def initialize(self):
        """Initialize the bot (non-blocking)."""
        await self.application.initialize()
        await self.application.start()
        asyncio.create_task(self.orchestrator.start_outbox_processor(interval=5.0))

    async def shutdown(self):
        """Shutdown the bot."""
        self.orchestrator.stop_outbox_processor()
        await self.application.stop()
        await self.application.shutdown()

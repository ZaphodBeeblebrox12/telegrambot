"""
Telegram Bot Integration - Fully Config-Driven with Outbox Pattern

Receives messages, processes through orchestrator.
Uses python-telegram-bot v20+ (async)

Pipeline: CONFIG → ROUTER → EXECUTOR → SERVICE → FORMATTER → MAPPING → OUTBOX → PUBLISHER
"""
import logging
import os
from typing import Optional

from telegram import Update, ReplyParameters
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

from config.config_loader import config
from orchestration.orchestrator import get_orchestrator
from messaging.message_mapping_service import get_mapping_service
from core.repositories import RepositoryFactory

logger = logging.getLogger(__name__)


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

        # Build application
        self.application = Application.builder().token(self.token).build()
        self._setup_handlers()

        logger.info("TradingBot initialized (config-driven v2.0 with outbox)")

    def _setup_handlers(self):
        """Setup message handlers."""
        self.application.add_handler(CommandHandler("start", self._cmd_start))
        self.application.add_handler(CommandHandler("help", self._cmd_help))
        self.application.add_handler(CommandHandler("status", self._cmd_status))

        self.application.add_handler(MessageHandler(filters.PHOTO, self._handle_image))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

        self.application.add_error_handler(self._handle_error)

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        await update.message.reply_text(
            "🤖 Trading Bot Ready (Config-Driven v2.0 + Outbox)\n\n"
            "Send me a chart image to create a trade setup\n"
            "Reply to trade messages with update commands.\n\n"
            "Available: trail, closed, target, stopped,\n"
            "breakeven, partial, closehalf, pyramid, note, cancel"
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        handlers = self.orchestrator.executor.list_handlers()
        await update.message.reply_text(
            f"🤖 Config-Driven Trading Bot v{config.system.version}\n\n"
            f"Handlers: {', '.join(handlers[:8])}...\n\n"
            "Reply to any trade message with:\n"
            "• trail <price> - Update trailing stop\n"
            "• closed <price> - Close trade\n"
            "• target <price> - Target hit\n"
            "• stopped <price> - Stopped out\n"
            "• breakeven - Close at breakeven\n"
            "• partial <price> [<pct>] - Partial close (FIFO)\n"
            "• closehalf <price> - Close 50% (FIFO)\n"
            "• pyramid <price> [<size%>] - Add to position\n"
            "• note <text> - Add note\n"
            "• cancel [<reason>] - Cancel trade"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        status = self.orchestrator.get_system_status()

        msg = (
            f"📊 System Status\n"
            f"Version: {status['config_version']}\n"
            f"Handlers: {len(status['handlers_registered'])}\n"
            f"Total Trades: {status['trade_stats']['total_trades']}\n"
            f"Open Trades: {status['trade_stats']['open_trades']}\n"
            f"Mappings: {status['mappings_count']}\n"
            f"Outbox Pending: {status['outbox_pending']}"
        )
        await update.message.reply_text(msg)

    async def _handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle chart image - create trade setup with outbox."""
        try:
            processing_msg = await update.message.reply_text("📊 Analyzing chart...")

            # Get image data
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            image_data = await file.download_as_bytearray()

            # Process through orchestrator (with outbox)
            result = await self.orchestrator.process_image(
                image_bytes=bytes(image_data),
                admin_channel_id=update.effective_chat.id,
                message_id=update.message.message_id
            )

            if not result['success']:
                error_msg = result['errors'][0] if result['errors'] else "Unknown error"
                await processing_msg.edit_text(f"❌ {error_msg}")
                return

            # Success
            trade = result['trade']
            outbox_count = len(result.get('outbox_ids', []))

            await processing_msg.edit_text(
                f"✅ Trade created: {trade.symbol}\n"
                f"ID: {trade.trade_id}\n"
                f"Queued to {outbox_count} destinations"
            )

            logger.info(f"Trade created: {trade.trade_id}")

        except Exception as e:
            logger.exception("Error processing image")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text commands."""
        text = update.message.text.strip()

        replied_msg_id = None
        if update.message.reply_to_message:
            replied_msg_id = update.message.reply_to_message.message_id

        if replied_msg_id:
            await self._process_command(update, context, text, replied_msg_id)
            return

        if text.startswith('/'):
            return

        await update.message.reply_text(
            "📤 Send me a chart image to create a trade\n"
            "Or reply to a trade message with a command"
        )

    async def _process_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        command_text: str,
        reply_to_msg_id: int
    ):
        """Process command through orchestrator with outbox."""
        try:
            result = await self.orchestrator.process_command(
                command_text=command_text,
                reply_to_message_id=reply_to_msg_id,
                admin_channel_id=update.effective_chat.id
            )

            if not result['success']:
                error_msg = result['errors'][0] if result['errors'] else "Command failed"
                await update.message.reply_text(f"❌ {error_msg}")
                return

            # Get formatted message
            tg_text = result['formatted'].get('telegram', 'Update processed')

            # Send response
            await update.message.reply_text(
                tg_text,
                reply_parameters=ReplyParameters(message_id=reply_to_msg_id)
            )

            # Delete command if configured
            cmd_config = config.commands.get('/update')
            if cmd_config and cmd_config.delete_command:
                try:
                    await update.message.delete()
                except Exception:
                    pass

            logger.info(f"Command processed: {command_text}")

        except Exception as e:
            logger.exception("Error processing command")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def _handle_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors."""
        logger.error(f"Update {update} caused error {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again."
            )

    async def post_init(self, application: Application):
        """Post-initialization hook - start outbox processor."""
        logger.info("Starting outbox processor...")
        # Start outbox processor in background
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

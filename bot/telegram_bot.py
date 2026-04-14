"""
Telegram Bot Integration - Config-Driven

Receives messages, processes commands through CommandRouter.
Uses python-telegram-bot v20+ (async)

Pipeline Integration:
- Image → OCR → process_setup()
- Text/Command → process_command()
- MessageMappingService for threading
"""

import logging
import os
from decimal import Decimal
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

from core.db import Database
from core.services import TradeService
from core.repositories import TradeRepository
from orchestration.orchestrator import TradingPipeline
from orchestration.command_router import CommandRouter
from messaging.message_mapping_service import MessageMappingService
from ocr.ocr_service import OCRService

logger = logging.getLogger(__name__)


class TradingBot:
    """Telegram bot for trading signal processing - fully config-driven."""

    def __init__(
        self,
        token: str,
        config_path: str,
        db: Database
    ):
        self.token = token
        self.config_path = config_path
        self.db = db

        # Initialize services
        self.trade_service = TradeService(db)
        self.trade_repo = TradeRepository(db)
        self.pipeline = TradingPipeline(config_path, self.trade_service)
        self.mapping_service = MessageMappingService(db)
        self.ocr = OCRService()
        self.command_router = CommandRouter(config_path)

        # Build application
        self.application = Application.builder().token(token).build()
        self._setup_handlers()

        logger.info("TradingBot initialized (config-driven)")

    def _setup_handlers(self):
        """Setup message handlers - minimal, delegates to CommandRouter."""
        # Only core bot commands (not trade commands)
        self.application.add_handler(CommandHandler("start", self._cmd_start))

        # All other commands go through CommandRouter
        self.application.add_handler(CommandHandler("status", self._cmd_status))

        # Messages
        self.application.add_handler(MessageHandler(filters.PHOTO, self._handle_image))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

        # Errors
        self.application.add_error_handler(self._handle_error)

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command - static help."""
        await update.message.reply_text(
            "🤖 Trading Bot Ready\n\n"
            "Send me a chart image to create a trade setup\n"
            "Reply to trade messages with commands."
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - uses CommandRouter for dynamic commands."""
        args = context.args
        if not args:
            await update.message.reply_text("❌ Usage: /status <trade_id>")
            return

        trade_id = args[0].upper()
        status = self.trade_service.get_trade_status(trade_id)

        if not status:
            await update.message.reply_text(f"❌ Trade {trade_id} not found")
            return

        msg = (
            f"📊 Trade {status['trade_id']}\n"
            f"Symbol: {status['symbol']} ({status['side']})\n"
            f"Status: {status['status']}\n"
            f"Entries: {len(status['entries'])}\n"
            f"Remaining: {status['snapshot']['remaining_size']}"
        )
        await update.message.reply_text(msg)

    async def _handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle chart image - create trade setup."""
        try:
            processing_msg = await update.message.reply_text("📊 Analyzing chart...")

            # Get image data
            photo = update.message.photo[-1]  # Highest resolution
            file = await context.bot.get_file(photo.file_id)
            image_data = await file.download_as_bytearray()

            # OCR Analysis
            ocr_result = self.ocr.analyze_image(bytes(image_data))

            if not ocr_result.get("setup_found"):
                await processing_msg.edit_text("❌ No trade setup detected in image")
                return

            # Process through pipeline
            result = self.pipeline.process_setup(ocr_result)

            if not result.success:
                await processing_msg.edit_text(f"❌ Error: {result.error}")
                return

            # Get trade ID and internal ID
            trade_id = result.trade_id
            trade_status = self.trade_service.get_trade_status(trade_id)
            internal_id = trade_status.get("id", 0) if trade_status else 0

            # Send formatted response (reply to original image)
            sent_msg = await update.message.reply_text(
                result.telegram_text,
                reply_to_message_id=update.message.message_id
            )

            # Save mapping for threading - with full chain support
            self.mapping_service.save_mapping(
                trade_id=internal_id,
                platform="telegram",
                message_id=str(sent_msg.message_id),
                channel_id=str(update.effective_chat.id),
                message_type="trade_setup",
                parent_tg_msg_id=None,
                parent_main_msg_id=None,
                reply_to_message_id=str(update.message.message_id)
            )

            # Delete processing message
            await processing_msg.delete()

            logger.info(f"Trade setup created: {trade_id}")

        except Exception as e:
            logger.exception("Error processing image")
            await update.message.reply_text(f"❌ Error processing image: {str(e)}")

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text commands - fully config-driven through CommandRouter."""
        text = update.message.text.strip()

        # Check if this is a reply to a trade message
        replied_msg_id = None
        if update.message.reply_to_message:
            replied_msg_id = update.message.reply_to_message.message_id

        if replied_msg_id:
            # Try to find trade by message ID
            trade_id = await self._get_trade_id_from_message(replied_msg_id)
            if trade_id:
                await self._process_command_through_router(update, context, text, trade_id, str(replied_msg_id))
                return
            else:
                await update.message.reply_text("❌ Could not find trade for this message")
                return

        # Not a reply - try to parse as standalone command through router
        parsed = self.command_router.parse(text)
        if parsed:
            await update.message.reply_text(
                "❌ Please reply to a trade message to use this command"
            )
            return

        # Unknown text
        await update.message.reply_text(
            "📤 Send me a chart image to create a trade\n"
            "Or reply to a trade message with a command"
        )

    async def _process_command_through_router(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        command_text: str,
        trade_id: str,
        reply_to_msg_id: str
    ):
        """Process command through CommandRouter and Pipeline."""
        try:
            # Get trade status for symbol and internal ID
            trade_status = self.trade_service.get_trade_status(trade_id)
            if not trade_status:
                await update.message.reply_text("❌ Trade not found")
                return

            symbol = trade_status["symbol"]
            internal_id = trade_status.get("id", 0)

            # Process through pipeline
            result = self.pipeline.process_command(
                command_text=command_text,
                trade_id=trade_id,
                symbol=symbol
            )

            if not result.success:
                await update.message.reply_text(f"❌ Error: {result.error}")
                return

            # Resolve correct parent for nested replies (not always root)
            parent_info = self.mapping_service.resolve_reply_parent(
                trade_id=internal_id,
                platform="telegram",
                message_type=result.message_type,
                reply_to_msg_id=reply_to_msg_id
            )

            # Send response with proper threading
            reply_params = None
            if parent_info:
                from telegram import ReplyParameters
                reply_params = ReplyParameters(message_id=int(parent_info["message_id"]))

            sent_msg = await update.message.reply_text(
                result.telegram_text,
                reply_parameters=reply_params
            )

            # Save mapping with full chain info
            self.mapping_service.save_mapping(
                trade_id=internal_id,
                platform="telegram",
                message_id=str(sent_msg.message_id),
                channel_id=str(update.effective_chat.id),
                message_type=result.message_type or "update",
                parent_tg_msg_id=parent_info["message_id"] if parent_info else None,
                parent_main_msg_id=None,
                reply_to_message_id=reply_to_msg_id
            )

            logger.info(f"Update processed: {trade_id} - {command_text}")

        except Exception as e:
            logger.exception("Error processing command")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def _get_trade_id_from_message(self, message_id: int) -> Optional[str]:
        """
        Lookup trade_id by Telegram message ID.

        Flow:
        1. Query MessageMappingService → get internal trade_id (integer)
        2. Use TradeRepository → fetch TradeModel
        3. Return → trade.trade_id (string, human-readable ID)
        """
        try:
            # Step 1: Get internal trade_id from message mapping
            internal_trade_id = self.mapping_service.get_trade_by_message(
                platform="telegram",
                message_id=str(message_id)
            )

            if not internal_trade_id:
                logger.debug(f"No mapping found for message_id: {message_id}")
                return None

            # Step 2: Fetch TradeModel using TradeRepository
            from sqlalchemy import select
            from core.db import TradeModel

            with self.trade_repo.session() as session:
                stmt = select(TradeModel).where(TradeModel.id == internal_trade_id)
                trade_model = session.execute(stmt).scalar_one_or_none()

                if trade_model:
                    # Step 3: Return human-readable trade_id
                    logger.debug(f"Found trade {trade_model.trade_id} for message {message_id}")
                    return trade_model.trade_id
                else:
                    logger.warning(f"Trade with internal id {internal_trade_id} not found")
                    return None

        except Exception as e:
            logger.error(f"Error looking up trade by message: {e}")
            return None

    async def _handle_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors."""
        logger.error(f"Update {update} caused error {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again."
            )

    def run(self):
        """Start the bot (blocking)."""
        logger.info("Starting Telegram bot polling...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

    async def initialize(self):
        """Initialize the bot (non-blocking)."""
        await self.application.initialize()
        await self.application.start()

    async def shutdown(self):
        """Shutdown the bot."""
        await self.application.stop()
        await self.application.shutdown()

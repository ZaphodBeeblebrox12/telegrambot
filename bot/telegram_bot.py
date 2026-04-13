"""
Telegram Bot Integration

Receives messages, processes commands, interacts with TradingPipeline.
Uses python-telegram-bot v20+ (async)
"""

import logging
import os
from decimal import Decimal
from typing import Optional

from telegram import Update, ReplyParameters
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

from core.db import Database
from core.services import TradeService
from orchestration.orchestrator import TradingPipeline
from messaging.message_mapping_service import MessageMappingService
from ocr.ocr_service import OCRService

logger = logging.getLogger(__name__)


class TradingBot:
    """Telegram bot for trading signal processing"""

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
        self.pipeline = TradingPipeline(config_path, self.trade_service)
        self.mapping_service = MessageMappingService(db)
        self.ocr = OCRService()

        # Track admin channel
        self.admin_channel_id: Optional[int] = None

        # Build application
        self.application = Application.builder().token(token).build()
        self._setup_handlers()

    def _setup_handlers(self):
        """Setup message handlers"""
        # Commands
        self.application.add_handler(CommandHandler("start", self._cmd_start))
        self.application.add_handler(CommandHandler("update", self._cmd_update))

        # Messages
        self.application.add_handler(MessageHandler(filters.PHOTO, self._handle_image))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

        # Errors
        self.application.add_error_handler(self._handle_error)

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        await update.message.reply_text(
            "🤖 Trading Bot Ready\n\n"
            "Send me a chart image to create a trade setup\n"
            "Or use /update <command> to update existing trades"
        )

    async def _cmd_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /update commands"""
        try:
            # Extract command text (remove "/update ")
            full_text = update.message.text
            if not full_text or len(full_text) < 8:
                await update.message.reply_text("❌ Usage: /update <command>")
                return

            command_text = full_text[8:].strip()  # Remove "/update "

            # Need trade_id from context or reply
            trade_id = await self._get_trade_id_from_context(update, context)
            if not trade_id:
                await update.message.reply_text("❌ No trade context. Reply to a trade message.")
                return

            # Get symbol from trade
            trade_status = self.trade_service.get_trade_status(trade_id)
            if not trade_status:
                await update.message.reply_text("❌ Trade not found")
                return

            symbol = trade_status["symbol"]

            # Process through pipeline
            result = self.pipeline.process_command(
                command_text=command_text,
                trade_id=trade_id,
                symbol=symbol
            )

            if not result.success:
                await update.message.reply_text(f"❌ Error: {result.error}")
                return

            # Send response
            sent_msg = await update.message.reply_text(
                result.telegram_text,
                reply_parameters=ReplyParameters(message_id=update.message.message_id)
            )

            # Save mapping
            self.mapping_service.save_mapping(
                trade_id=trade_status.get("id", 0),
                platform="telegram",
                message_id=str(sent_msg.message_id),
                channel_id=str(update.effective_chat.id),
                message_type=result.message_type or "update",
                parent_message_id=str(update.message.message_id)
            )

            logger.info(f"Update processed: {trade_id} - {command_text}")

        except Exception as e:
            logger.exception("Error processing update command")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def _handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle chart image - create trade setup"""
        try:
            await update.message.reply_text("📊 Analyzing chart...")

            # Get image data
            photo = update.message.photo[-1]  # Highest resolution
            file = await context.bot.get_file(photo.file_id)
            image_data = await file.download_as_bytearray()

            # OCR Analysis
            ocr_result = self.ocr.analyze_image(bytes(image_data))

            if not ocr_result.get("setup_found"):
                await update.message.reply_text("❌ No trade setup detected in image")
                return

            # Process through pipeline
            result = self.pipeline.process_setup(ocr_result)

            if not result.success:
                await update.message.reply_text(f"❌ Error: {result.error}")
                return

            # Send formatted response
            sent_msg = await update.message.reply_text(result.telegram_text)

            # Get trade ID and internal ID
            trade_id = result.trade_id
            trade_status = self.trade_service.get_trade_status(trade_id)
            internal_id = trade_status.get("id", 0) if trade_status else 0

            # Save mapping
            self.mapping_service.save_mapping(
                trade_id=internal_id,
                platform="telegram",
                message_id=str(sent_msg.message_id),
                channel_id=str(update.effective_chat.id),
                message_type="trade_setup",
                parent_message_id=None
            )

            logger.info(f"Trade setup created: {trade_id}")

        except Exception as e:
            logger.exception("Error processing image")
            await update.message.reply_text(f"❌ Error processing image: {str(e)}")

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle plain text (treat as command if looks like one)"""
        text = update.message.text.strip().upper()

        # Check if it looks like a command
        commands = ["TRAIL", "CLOSE", "PARTIAL", "TARGET", "STOP", "PYRAMID", "CANCEL"]
        if any(text.startswith(cmd) for cmd in commands):
            # Treat as implicit /update
            update.message.text = f"/update {text}"
            return await self._cmd_update(update, context)

        # Otherwise ignore or help
        await update.message.reply_text(
            "📤 Send me a chart image to create a trade\n"
            "Or use /update <command> for existing trades"
        )

    async def _get_trade_id_from_context(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE
    ) -> Optional[str]:
        """Extract trade_id from reply or context"""
        # Try to get from replied message
        if update.message.reply_to_message:
            reply_msg_id = update.message.reply_to_message.message_id

            # Query DB for trade by message ID
            # This is simplified - in production would query mapping table
            # For now, check if bot_data has it
            trade_id = context.bot_data.get(f"msg_{reply_msg_id}")
            if trade_id:
                return trade_id

        # Try to get from user_data
        return context.user_data.get("current_trade_id") if context.user_data else None

    async def _handle_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again."
            )

    def run(self):
        """Start the bot"""
        logger.info("Starting Telegram bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

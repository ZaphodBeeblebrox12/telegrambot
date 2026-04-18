"""Telegram Bot Handler - PRODUCTION VERSION (FIXED)
FIXED: Commands work exactly like old bot:
- /update targetmet (no price)
- /update closehalf (no price)
- /update trail 4800 (with price)
- /update stopped 4750 (with price)
- Case insensitive
- Uses config command_mapping
ADDED: Better debug logging
ADDED: resolve_trade_id_robust() for reply-based flow
"""
import os
import tempfile
import logging
from typing import Optional, Dict, Any
from telegram import Update
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

class TelegramBot:
    """Telegram bot with proper command handling"""

    def __init__(self):
        self.cfg = config
        self.orchestrator = get_orchestrator()
        self.mapping_service = get_mapping_service()
        self.db = RepositoryFactory.get_database()
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.admin_channel = int(os.getenv('ADMIN_CHANNEL_ID', '0'))

        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")
        if not self.admin_channel:
            raise ValueError("ADMIN_CHANNEL_ID not set")

        logger.info(f"TelegramBot initialized with admin channel: {self.admin_channel}")

    def resolve_trade_id_robust(self, update: Update) -> Optional[str]:
        """
        Resolve trade_id from reply context.

        CRITICAL: Commands ONLY work when replying to a trade message.
        This ensures proper thread tracking and prevents orphaned updates.
        """
        message = update.channel_post or update.message
        if not message:
            logger.debug("No message in update")
            return None

        # Check if this is a reply to another message
        replied_msg = getattr(message, 'reply_to_message', None)
        if not replied_msg:
            logger.debug("Message is not a reply")
            return None

        replied_id = replied_msg.message_id
        logger.debug(f"Message is reply to: {replied_id}")

        # Try to find mapping for the replied message
        mapping = self.mapping_service.get_mapping(replied_id)

        if mapping:
            logger.info(f"Found mapping for reply_to {replied_id}: trade_id={mapping.trade_id}")
            return mapping.trade_id

        # If no direct mapping, try to find any mapping that references this message
        # (for nested replies)
        all_mappings = self.mapping_service.get_all_mappings()
        for m in all_mappings:
            if m.parent_main_msg_id == replied_id:
                logger.info(f"Found parent mapping via parent_main_msg_id: trade_id={m.trade_id}")
                return m.trade_id

        logger.warning(f"No mapping found for reply_to message {replied_id}")
        return None

    async def handle_update_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /update command - EXACT old bot behavior.

        Commands must be replies to trade messages.
        Examples:
        - /update targetmet
        - /update closehalf
        - /update trail 4800
        - /update stopped 4750
        """
        message = update.channel_post or update.message
        if not message:
            return

        command_text = message.text or message.caption or ""
        message_id = message.message_id

        logger.info(f"=== HANDLE UPDATE COMMAND ===")
        logger.info(f"Command text: '{command_text}'")
        logger.info(f"Message ID: {message_id}")
        logger.info(f"Chat ID: {message.chat_id}")

        # Check if in admin channel
        if message.chat_id != self.admin_channel:
            logger.warning(f"Command from non-admin channel: {message.chat_id}")
            return

        # Get reply_to_message_id
        reply_to_id = None
        replied_msg = getattr(message, 'reply_to_message', None)
        if replied_msg:
            reply_to_id = replied_msg.message_id
            logger.info(f"Reply to message: {reply_to_id}")

        # Check if this is an image update (has photo)
        is_image_update = bool(message.photo)
        photo_path = None

        if is_image_update:
            logger.info("Processing as image update (has photo)")
            # Download photo if present
            try:
                photo = message.photo[-1]  # Get largest photo
                file = await photo.get_file()
                photo_path = os.path.join(tempfile.gettempdir(), f"telegram_{message_id}.jpg")
                await file.download_to_drive(photo_path)
                logger.info(f"Downloaded photo to: {photo_path}")
            except Exception as e:
                logger.error(f"Failed to download photo: {e}")

        # Process command through orchestrator
        result = await self.orchestrator.process_command(
            command_text=command_text,
            reply_to_message_id=reply_to_id,
            admin_channel_id=self.admin_channel,
            photo_path=photo_path,
            is_image_update=is_image_update
        )

        logger.info(f"Orchestrator result: success={result['success']}, errors={result['errors']}")

        # Handle result
        if result['success']:
            if is_image_update:
                # Edit the image caption with the formatted update
                try:
                    await context.bot.edit_message_caption(
                        chat_id=self.admin_channel,
                        message_id=message_id,
                        caption=result['formatted_text']
                    )
                    logger.info(f"Edited image caption for message {message_id}")
                except Exception as e:
                    logger.error(f"Failed to edit image caption: {e}")
            else:
                # Delete the command message (text command)
                try:
                    await context.bot.delete_message(
                        chat_id=self.admin_channel,
                        message_id=message_id
                    )
                    logger.info(f"Deleted command message {message_id}")
                except Exception as e:
                    logger.warning(f"Could not delete command message: {e}")
        else:
            # Send error message
            error_text = "\n".join(result['errors']) if result['errors'] else "Command failed"
            try:
                error_msg = await context.bot.send_message(
                    chat_id=self.admin_channel,
                    text=f"❌ {error_text}",
                    reply_to_message_id=message_id
                )
                # Auto-delete error after 5 seconds
                import asyncio
                await asyncio.sleep(5)
                await context.bot.delete_message(
                    chat_id=self.admin_channel,
                    message_id=error_msg.message_id
                )
            except Exception as e:
                logger.error(f"Failed to send error message: {e}")

        logger.info(f"=== HANDLE UPDATE COMMAND COMPLETE ===")

    async def handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle image posted in admin channel (trade setup)"""
        message = update.channel_post or update.message
        if not message or not message.photo:
            return

        logger.info(f"=== HANDLE IMAGE ===")
        logger.info(f"Message ID: {message.message_id}")

        # Check if in admin channel
        if message.chat_id != self.admin_channel:
            logger.debug(f"Image from non-admin channel: {message.chat_id}")
            return

        # Check for /update in caption
        caption = message.caption or ""
        if '/update' in caption.lower():
            logger.info("Image has /update in caption, routing to update handler")
            await self.handle_update_command(update, context)
            return

        # Check for /watch command
        if caption.lower().startswith('/watch'):
            logger.info("Image has /watch command, routing to watchlist handler")
            await self.handle_watch_command(update, context)
            return

        # Process as new trade setup
        try:
            photo = message.photo[-1]
            file = await photo.get_file()
            photo_bytes = await file.download_as_bytearray()
            photo_path = os.path.join(tempfile.gettempdir(), f"telegram_setup_{message.message_id}.jpg")
            await file.download_to_drive(photo_path)

            result = await self.orchestrator.process_image(
                image_bytes=bytes(photo_bytes),
                admin_channel_id=self.admin_channel,
                message_id=message.message_id,
                photo_path=photo_path
            )

            if result['success'] and result['formatted_text']:
                # Edit the admin message with formatted trade setup
                try:
                    await context.bot.edit_message_caption(
                        chat_id=self.admin_channel,
                        message_id=message.message_id,
                        caption=result['formatted_text']
                    )
                    logger.info(f"Edited admin message {message.message_id} with trade setup")
                except Exception as e:
                    logger.error(f"Failed to edit admin message: {e}")

            if not result['success']:
                logger.warning(f"Image processing failed: {result['errors']}")

        except Exception as e:
            logger.error(f"Error processing image: {e}", exc_info=True)

        logger.info(f"=== HANDLE IMAGE COMPLETE ===")

    async def handle_watch_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /watch command for watchlist"""
        message = update.channel_post or update.message
        if not message:
            return

        logger.info("=== HANDLE WATCH COMMAND ===")
        # Watchlist implementation would go here
        # For now, just log it
        logger.info("Watch command received (implementation pending)")
        logger.info(f"=== HANDLE WATCH COMMAND COMPLETE ===")

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        message = update.channel_post or update.message
        if not message:
            return

        help_text = """
🤖 **Trading Bot Commands**

**Trade Setup:**
Post chart image in admin channel
Bot analyzes and forwards to Telegram + Twitter

**Position Updates (reply to trade):**
• `/update targetmet` - Target hit
• `/update closehalf` - Close 50%
• `/update trail 4800` - Trailing stop
• `/update stopped 4750` - Stopped out
• `/update closed 4800` - Close trade
• `/update breakeven` - Close at entry
• `/update partial 4800 25` - Close 25%
• `/update note watching resistance` - Add note
• `/update cancel` - Cancel trade

**Requirements:**
- Commands must reply to trade message
- Case insensitive
- Price optional for some commands
        """

        await message.reply_text(help_text)

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        message = update.channel_post or update.message
        if not message:
            return

        status = self.orchestrator.get_system_status()
        status_text = f"""
📊 **System Status**

Version: {status['config_version']}
Handlers: {', '.join(status['handlers_registered'])}
Mappings: {status['mappings_count']}
        """

        await message.reply_text(status_text)

    async def start(self):
        """Start the bot"""
        logger.info("Starting Telegram Bot...")

        app = Application.builder().token(self.token).build()

        # Command handlers
        app.add_handler(CommandHandler("update", self.handle_update_command, 
                                       filters=filters.Chat(chat_id=self.admin_channel)))
        app.add_handler(CommandHandler("help", self.handle_help,
                                       filters=filters.Chat(chat_id=self.admin_channel)))
        app.add_handler(CommandHandler("status", self.handle_status,
                                       filters=filters.Chat(chat_id=self.admin_channel)))

        # Message handlers
        app.add_handler(MessageHandler(
            filters.Chat(chat_id=self.admin_channel) & filters.PHOTO,
            self.handle_image
        ))

        # Error handler
        app.add_error_handler(self._error_handler)

        # Start outbox processor in background
        import asyncio
        asyncio.create_task(self.orchestrator.start_outbox_processor(interval=5.0))

        logger.info("Bot started, beginning polling...")
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.orchestrator.stop_outbox_processor()
            await app.stop()

    async def _error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Error: {context.error}", exc_info=True)

    def run(self):
        """Run the bot (synchronous wrapper for start)."""
        import asyncio
        asyncio.run(self.start())

# BACKWARD COMPATIBILITY: main.py imports TradingBot
TradingBot = TelegramBot

# Singleton
_bot = None

def get_telegram_bot() -> TelegramBot:
    global _bot
    if _bot is None:
        _bot = TelegramBot()
    return _bot


# BACKWARD COMPATIBILITY: main.py imports TradingBot
TradingBot = TelegramBot

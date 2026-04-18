"""Telegram Bot Handler - FIXED v8
FIXED: Background thread sends Telegram messages INSTANTLY, isolated from orchestrator blocking.
FIXED: Uses threading + dedicated event loop for Telegram sends.
"""
import os
import tempfile
import logging
import asyncio
import threading
from typing import Optional
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)

from config.config_loader import config
from orchestration.orchestrator import get_orchestrator
from messaging.message_mapping_service import get_mapping_service
from core.repositories import RepositoryFactory

logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self):
        self.cfg = config
        self.orchestrator = get_orchestrator()
        self.mapping_service = get_mapping_service()
        self.db = RepositoryFactory.get_database()
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.admin_channel = int(os.getenv('ADMIN_CHANNEL_ID', '0'))
        self._app = None
        self._publisher = None
        self._send_loop = None
        self._send_thread = None

        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")
        if not self.admin_channel:
            raise ValueError("ADMIN_CHANNEL_ID not set")

        logger.info(f"TelegramBot initialized with admin channel: {self.admin_channel}")

    def _start_send_thread(self):
        if self._send_thread is not None and self._send_thread.is_alive():
            return
        def _run_loop():
            self._send_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._send_loop)
            self._send_loop.run_forever()
        self._send_thread = threading.Thread(target=_run_loop, daemon=True, name="TelegramSender")
        self._send_thread.start()
        logger.info("Started dedicated Telegram sender thread")

    def _send_in_thread(self, payload: dict):
        if not self._send_loop:
            logger.error("Send loop not ready")
            return
        async def _do_send():
            if not self._publisher or not self._publisher._bot:
                logger.error("Publisher not ready")
                return
            channel_id = payload.get('channel_id')
            text = payload.get('text')
            reply_to = payload.get('reply_to_message_id')
            photo_path = payload.get('photo_path')
            try:
                logger.debug(f"[BG] Sending to {channel_id}")
                if photo_path and os.path.exists(photo_path):
                    with open(photo_path, 'rb') as f:
                        photo_bytes = f.read()
                    await self._publisher.send_photo(channel_id=channel_id, photo=photo_bytes, caption=text, reply_to_message_id=reply_to)
                else:
                    await self._publisher.send_message(channel_id=channel_id, text=text, reply_to_message_id=reply_to)
                logger.info(f"[BG] Sent to {channel_id} OK")
            except Exception as e:
                logger.error(f"[BG] Send failed: {e}")
        asyncio.run_coroutine_threadsafe(_do_send(), self._send_loop)

    def _ensure_handler_registered(self):
        if 'telegram' in self.orchestrator.outbox.processor.handlers:
            return
        from publishers.telegram_publisher import get_telegram_publisher
        self._publisher = get_telegram_publisher()
        async def _handler(payload: dict):
            self._send_in_thread(payload)
        self.orchestrator.outbox.register_handler('telegram', _handler)
        logger.info("=" * 50)
        logger.info("OUTBOX HANDLER REGISTERED for destination: telegram")
        logger.info("=" * 50)

    def resolve_trade_id_robust(self, update: Update) -> Optional[str]:
        message = update.channel_post or update.message
        if not message:
            return None
        replied_msg = getattr(message, 'reply_to_message', None)
        if not replied_msg:
            return None
        replied_id = replied_msg.message_id
        mapping = self.mapping_service.get_mapping(replied_id)
        if mapping:
            return mapping.trade_id
        all_mappings = self.mapping_service.get_all_mappings()
        for m in all_mappings:
            if m.parent_main_msg_id == replied_id:
                return m.trade_id
        return None

    async def handle_update_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.channel_post or update.message
        if not message:
            return
        command_text = message.text or message.caption or ""
        message_id = message.message_id
        if message.chat_id != self.admin_channel:
            return
        reply_to_id = None
        replied_msg = getattr(message, 'reply_to_message', None)
        if replied_msg:
            reply_to_id = replied_msg.message_id
        is_image_update = bool(message.photo)
        photo_path = None
        if is_image_update:
            try:
                photo = message.photo[-1]
                file = await photo.get_file()
                photo_path = os.path.join(tempfile.gettempdir(), f"telegram_{message_id}.jpg")
                await file.download_to_drive(photo_path)
            except Exception as e:
                logger.error(f"Failed to download photo: {e}")
        result = await self.orchestrator.process_command(
            command_text=command_text,
            reply_to_message_id=reply_to_id,
            admin_channel_id=self.admin_channel,
            photo_path=photo_path,
            is_image_update=is_image_update
        )
        if result['success']:
            if is_image_update:
                try:
                    await context.bot.edit_message_caption(chat_id=self.admin_channel, message_id=message_id, caption=result['formatted_text'])
                except Exception as e:
                    logger.error(f"Failed to edit image caption: {e}")
            else:
                try:
                    await context.bot.delete_message(chat_id=self.admin_channel, message_id=message_id)
                except Exception as e:
                    logger.warning(f"Could not delete command message: {e}")
        else:
            error_text = "\n".join(result['errors']) if result['errors'] else "Command failed"
            try:
                error_msg = await context.bot.send_message(chat_id=self.admin_channel, text=f"❌ {error_text}", reply_to_message_id=message_id)
                await asyncio.sleep(5)
                await context.bot.delete_message(chat_id=self.admin_channel, message_id=error_msg.message_id)
            except Exception as e:
                logger.error(f"Failed to send error message: {e}")

    async def handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.channel_post or update.message
        if not message or not message.photo:
            return
        if message.chat_id != self.admin_channel:
            return
        caption = message.caption or ""
        if '/update' in caption.lower():
            await self.handle_update_command(update, context)
            return
        if caption.lower().startswith('/watch'):
            await self.handle_watch_command(update, context)
            return
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
                try:
                    await context.bot.edit_message_caption(chat_id=self.admin_channel, message_id=message.message_id, caption=result['formatted_text'])
                except Exception as e:
                    logger.error(f"Failed to edit admin message: {e}")
        except Exception as e:
            logger.error(f"Error processing image: {e}", exc_info=True)

    async def handle_watch_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.channel_post or update.message
        if not message:
            return
        logger.info("Watch command received (implementation pending)")

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.channel_post or update.message
        if not message:
            return
        help_text = (
            "🤖 **Trading Bot Commands**\n\n"
            "**Trade Setup:**\nPost chart image in admin channel\n"
            "Bot analyzes and forwards to Telegram + Twitter\n\n"
            "**Position Updates (reply to trade):**\n"
            "• `/update targetmet` - Target hit\n"
            "• `/update closehalf` - Close 50%\n"
            "• `/update trail 4800` - Trailing stop\n"
            "• `/update stopped 4750` - Stopped out\n"
            "• `/update closed 4800` - Close trade\n"
            "• `/update breakeven` - Close at entry\n"
            "• `/update partial 4800 25` - Close 25%\n"
            "• `/update note watching resistance` - Add note\n"
            "• `/update cancel` - Cancel trade\n\n"
            "**Requirements:**\n"
            "- Commands must reply to trade message\n"
            "- Case insensitive\n"
            "- Price optional for some commands"
        )
        await message.reply_text(help_text)

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.channel_post or update.message
        if not message:
            return
        status = self.orchestrator.get_system_status()
        status_text = (
            f"📊 **System Status**\n\n"
            f"Version: {status['config_version']}\n"
            f"Handlers: {', '.join(status['handlers_registered'])}\n"
            f"Mappings: {status['mappings_count']}"
        )
        await message.reply_text(status_text)

    async def start(self):
        logger.info("Starting Telegram Bot...")
        self._app = Application.builder().token(self.token).build()
        self._ensure_handler_registered()
        self._start_send_thread()
        if self._publisher:
            self._publisher.set_bot(self._app.bot)
            logger.info("TelegramPublisher bot instance set")
        import asyncio
        asyncio.create_task(self.orchestrator.start_outbox_processor(interval=5.0))
        app = self._app
        app.add_handler(CommandHandler("update", self.handle_update_command, filters=filters.Chat(chat_id=self.admin_channel)))
        app.add_handler(CommandHandler("help", self.handle_help, filters=filters.Chat(chat_id=self.admin_channel)))
        app.add_handler(CommandHandler("status", self.handle_status, filters=filters.Chat(chat_id=self.admin_channel)))
        app.add_handler(MessageHandler(filters.Chat(chat_id=self.admin_channel) & filters.PHOTO, self.handle_image))
        app.add_error_handler(self._error_handler)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        logger.info("Bot polling started. Waiting for messages...")
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.orchestrator.stop_outbox_processor()
            await app.stop()

    async def _error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}", exc_info=True)

    def run(self):
        import asyncio
        asyncio.run(self.start())

# BACKWARD COMPATIBILITY
TradingBot = TelegramBot
_bot = None

def get_telegram_bot() -> TelegramBot:
    global _bot
    if _bot is None:
        _bot = TelegramBot()
    return _bot

TradingBot = TelegramBot

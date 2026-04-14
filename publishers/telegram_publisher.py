"""Config-driven Telegram Publisher"""
import os
from typing import Optional, Dict, Any, List
import asyncio
from telegram import Bot
from telegram.constants import ParseMode

from config.config_loader import config


class TelegramPublisher:
    """Publishes messages to Telegram channels - Config-driven"""

    def __init__(self):
        self.cfg = config
        self.destinations = config.destinations
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self._bot: Optional[Bot] = None

    async def _get_bot(self) -> Bot:
        """Get or create bot instance"""
        if self._bot is None:
            if not self.bot_token:
                raise ValueError("TELEGRAM_BOT_TOKEN not set")
            self._bot = Bot(token=self.bot_token)
        return self._bot

    def get_destination_channels(self, pair: str = 'pair1') -> List[Dict[str, Any]]:
        """Get Telegram channels for destination pair"""
        channels = []
        platform_cfg = self.cfg.platform_settings.get(pair, {})

        for dest_id, dest_cfg in self.destinations.items():
            if dest_cfg.platform == 'telegram':
                channels.append({
                    'id': dest_id,
                    'channel_id': dest_cfg.channel_id,
                    'display_name': dest_cfg.display_name
                })

        return channels

    async def send_message(
        self,
        channel_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        parse_mode: str = ParseMode.HTML,
        **kwargs
    ) -> Dict[str, Any]:
        """Send message to Telegram channel"""
        bot = await self._get_bot()

        message = await bot.send_message(
            chat_id=channel_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            parse_mode=parse_mode
        )

        return {
            'message_id': message.message_id,
            'chat_id': message.chat_id,
            'text': message.text
        }

    async def send_photo(
        self,
        channel_id: int,
        photo: bytes,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Send photo to Telegram channel"""
        bot = await self._get_bot()

        message = await bot.send_photo(
            chat_id=channel_id,
            photo=photo,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
            parse_mode=ParseMode.HTML
        )

        return {
            'message_id': message.message_id,
            'chat_id': message.chat_id,
            'caption': message.caption
        }

    async def edit_message(
        self,
        channel_id: int,
        message_id: int,
        text: str,
        **kwargs
    ) -> bool:
        """Edit existing message"""
        try:
            bot = await self._get_bot()
            await bot.edit_message_text(
                chat_id=channel_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML
            )
            return True
        except Exception:
            return False

    async def delete_message(
        self,
        channel_id: int,
        message_id: int,
        **kwargs
    ) -> bool:
        """Delete message"""
        try:
            bot = await self._get_bot()
            await bot.delete_message(
                chat_id=channel_id,
                message_id=message_id
            )
            return True
        except Exception:
            return False

    def should_post_to_telegram(self, message_type: str, pair: str = 'pair1') -> bool:
        """Check if message type should post to Telegram"""
        msg_type_cfg = config.get_message_type(message_type)
        if not msg_type_cfg:
            return False

        platform_rules = msg_type_cfg.platform_rules
        return platform_rules.get('telegram', False)


# Singleton
_publisher: Optional[TelegramPublisher] = None

def get_telegram_publisher() -> TelegramPublisher:
    global _publisher
    if _publisher is None:
        _publisher = TelegramPublisher()
    return _publisher

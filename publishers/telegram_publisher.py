"""Telegram Publisher – Real implementation using the bot instance."""

import logging
from typing import List, Dict, Any, Optional

from config.config_loader import config

logger = logging.getLogger(__name__)


class TelegramPublisher:
    """Publishes messages to Telegram channels using the bot."""

    def __init__(self):
        self._bot = None  # Will be set by the bot instance

    def set_bot(self, bot):
        """Set the Telegram bot instance (called by TradingBot)."""
        self._bot = bot

    def get_destination_channels(self) -> List[Dict[str, Any]]:
        channels = []
        for dest_id, dest in config.destinations.items():
            if dest.get("platform") == "telegram":
                channels.append(
                    {
                        "dest_id": dest_id,
                        "channel_id": dest.get("channel_id"),
                        "display_name": dest.get("display_name"),
                    }
                )
        return channels

    async def send_message(
        self,
        channel_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
    ) -> Optional[int]:
        """Send a text message to a Telegram channel."""
        if not self._bot:
            logger.error("TelegramPublisher: bot not set")
            return None

        try:
            msg = await self._bot.send_message(
                chat_id=channel_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
                parse_mode="HTML",
            )
            return msg.message_id
        except Exception as e:
            logger.exception(f"Failed to send Telegram message to {channel_id}")
            raise

    async def send_photo(
        self,
        channel_id: int,
        photo: bytes,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Optional[int]:
        """Send a photo to a Telegram channel."""
        if not self._bot:
            logger.error("TelegramPublisher: bot not set")
            return None

        try:
            msg = await self._bot.send_photo(
                chat_id=channel_id,
                photo=photo,
                caption=caption,
                reply_to_message_id=reply_to_message_id,
            )
            return msg.message_id
        except Exception as e:
            logger.exception(f"Failed to send Telegram photo to {channel_id}")
            raise


_tg_publisher = None


def get_telegram_publisher() -> TelegramPublisher:
    global _tg_publisher
    if _tg_publisher is None:
        _tg_publisher = TelegramPublisher()
    return _tg_publisher
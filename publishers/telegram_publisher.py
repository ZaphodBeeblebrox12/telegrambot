"""Telegram Publisher"""
from typing import List, Dict, Any, Optional
from config.config_loader import config

class TelegramPublisher:
    """Publishes messages to Telegram channels"""

    def get_destination_channels(self) -> List[Dict[str, Any]]:
        channels = []
        for dest_id, dest in config.destinations.items():
            if dest.get('platform') == 'telegram':
                channels.append({
                    'dest_id': dest_id,
                    'channel_id': dest.get('channel_id'),
                    'display_name': dest.get('display_name')
                })
        return channels

    async def send_message(
        self,
        channel_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None
    ) -> Optional[int]:
        print(f"[TELEGRAM] To {channel_id}: {text[:50]}...")
        return 12345

    async def send_photo(
        self,
        channel_id: int,
        photo: bytes,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None
    ) -> Optional[int]:
        print(f"[TELEGRAM] Photo to {channel_id}")
        return 12345

_tg_publisher = None

def get_telegram_publisher():
    global _tg_publisher
    if _tg_publisher is None:
        _tg_publisher = TelegramPublisher()
    return _tg_publisher

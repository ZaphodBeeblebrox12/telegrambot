"""Message Mapping Service - SQL-based with target message tracking for reply threading"""
import json
import logging
import time
from typing import Optional, List, Dict, Any

from config.config_loader import config
from core.models import MessageMapping
from core.repositories import RepositoryFactory

logger = logging.getLogger(__name__)


class MessageMappingService:
    """Manages message mappings for thread tracking with target channel message ID persistence."""

    def __init__(self):
        self.cfg = config
        self.repo = RepositoryFactory.get_mapping_repository()

    def create_mapping(
        self,
        main_msg_id: int,
        tg_channel: int,
        twitter: Optional[Dict] = None,
        trade_id: Optional[str] = None,
        ocr_symbol: Optional[str] = None,
        asset_class: Optional[str] = None,
        leverage_multiplier: int = 1,
        **kwargs
    ) -> MessageMapping:
        mapping = MessageMapping(
            main_msg_id=main_msg_id,
            tg_channel=tg_channel,
            twitter=twitter or {},
            trade_id=trade_id,
            ocr_symbol=ocr_symbol,
            asset_class=asset_class,
            leverage_multiplier=leverage_multiplier,
            **kwargs
        )
        self.repo.save(mapping)
        return mapping

    def get_mapping(self, main_msg_id: int) -> Optional[MessageMapping]:
        return self.repo.get(main_msg_id)

    def get_mapping_by_trade(self, trade_id: str) -> Optional[MessageMapping]:
        return self.repo.get_by_trade_id(trade_id)

    def get_all_mappings(self) -> List[MessageMapping]:
        return self.repo.get_all()

    def add_target_message(self, trade_id: str, channel_id: int, message_id: int):
        """Store a target channel message ID for reply threading.

        Uses trade_id as the lookup key so reply threading works across
        multiple messages for the same trade.
        """
        if not trade_id or not channel_id or not message_id:
            return

        _store_target_message(trade_id, channel_id, message_id)

    def get_last_target_message(self, trade_id: str, channel_id: int) -> Optional[int]:
        """Find the most recent message ID for a trade in a specific target channel."""
        if not trade_id or not channel_id:
            return None
        return _get_last_target_message(trade_id, channel_id)

    def get_chain(self, trade_id: str) -> List[MessageMapping]:
        """Get all mappings for a trade (the chain)."""
        if not trade_id:
            return []
        try:
            all_mappings = self.repo.get_all()
            return [m for m in all_mappings if m.trade_id == trade_id]
        except Exception as e:
            logger.error(f"Error fetching chain for trade {trade_id}: {e}")
            return []

    def update_mapping(self, message_id: int, **updates) -> bool:
        """Update mapping fields via repository."""
        mapping = self.get_mapping(message_id)
        if not mapping:
            return False
        for key, value in updates.items():
            if hasattr(mapping, key):
                setattr(mapping, key, value)
        self.repo.save(mapping)
        return True

    def delete_mapping(self, message_id: int) -> bool:
        """Delete a mapping."""
        return self.repo.delete(message_id)

    def get_stats(self) -> dict:
        """Get mapping statistics."""
        try:
            all_mappings = self.repo.get_all()
            total = len(all_mappings)
            with_trade = sum(1 for m in all_mappings if m.trade_id)
            return {"total": total, "with_trade": with_trade}
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"total": 0, "with_trade": 0}


# --- Target message tracking (JSON-backed, no DB schema changes) ---
_TARGET_MESSAGES_FILE = "target_messages.json"
_target_messages_cache = None


def _load_target_messages() -> Dict:
    """Load target message tracking from JSON file."""
    global _target_messages_cache
    if _target_messages_cache is not None:
        return _target_messages_cache
    try:
        import os
        if os.path.exists(_TARGET_MESSAGES_FILE):
            with open(_TARGET_MESSAGES_FILE, 'r') as f:
                _target_messages_cache = json.load(f)
                return _target_messages_cache
    except Exception as e:
        logger.warning(f"Could not load target messages: {e}")
    _target_messages_cache = {}
    return _target_messages_cache


def _save_target_messages(data: Dict):
    """Save target message tracking to JSON file."""
    global _target_messages_cache
    _target_messages_cache = data
    try:
        with open(_TARGET_MESSAGES_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save target messages: {e}")


def _store_target_message(trade_id: str, channel_id: int, message_id: int):
    """Store a target channel message ID keyed by trade_id."""
    data = _load_target_messages()

    if "by_trade" not in data:
        data["by_trade"] = {}

    tid = str(trade_id)
    if tid not in data["by_trade"]:
        data["by_trade"][tid] = []

    entry = {
        "channel_id": channel_id,
        "message_id": message_id,
        "timestamp": time.time()
    }
    data["by_trade"][tid].append(entry)

    _save_target_messages(data)
    logger.debug(f"Stored target msg {message_id} for channel {channel_id} (trade {trade_id})")


def _get_last_target_message(trade_id: str, channel_id: int) -> Optional[int]:
    """Get last message ID for a trade in a channel."""
    data = _load_target_messages()

    last_time = 0
    last_msg_id = None

    for entry in data.get("by_trade", {}).get(str(trade_id), []):
        if entry.get("channel_id") == channel_id:
            ts = entry.get("timestamp", 0)
            if ts > last_time:
                last_time = ts
                last_msg_id = entry.get("message_id")

    if last_msg_id:
        logger.debug(f"Found last target msg {last_msg_id} for trade {trade_id} in channel {channel_id}")

    return last_msg_id


_mapping_service = None


def get_mapping_service():
    global _mapping_service
    if _mapping_service is None:
        _mapping_service = MessageMappingService()
    return _mapping_service

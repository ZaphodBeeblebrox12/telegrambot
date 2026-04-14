"""Config-driven Message Mapping Service - SQL-based (FIX 6)"""
from typing import Optional, Dict, Any, List
from datetime import datetime

from config.config_loader import config
from core.models import MessageMapping
from core.repositories import RepositoryFactory

class MessageMappingService:
    """Manages message mappings for thread tracking - SQL-based (FIX 6)"""

    def __init__(self):
        self.cfg = config.reply_nesting
        self.mapping_cfg = config.message_mapping
        self.repo = RepositoryFactory.get_mapping_repository()

    def create_mapping(
        self,
        main_msg_id: int,
        tg_channel: int,
        trade_id: Optional[str] = None,
        ocr_symbol: Optional[str] = None,
        asset_class: Optional[str] = None,
        leverage_multiplier: int = 1,
        gemini_result: Optional[Dict] = None,
        is_position_update: bool = False,
        is_admin_channel: bool = False,
        parent_main_msg_id: Optional[int] = None,
        parent_tg_msg_id: Optional[int] = None
    ) -> MessageMapping:
        """Create new message mapping with SQL persistence (FIX 6)"""
        mapping = MessageMapping(
            main_msg_id=main_msg_id,
            tg_channel=tg_channel,
            trade_id=trade_id,
            ocr_symbol=ocr_symbol,
            asset_class=asset_class,
            leverage_multiplier=leverage_multiplier,
            gemini_result=gemini_result,
            is_position_update=is_position_update,
            is_admin_channel=is_admin_channel,
            parent_main_msg_id=parent_main_msg_id,
            parent_tg_msg_id=parent_tg_msg_id
        )

        self.repo.save(mapping)
        return mapping

    def get_mapping(self, main_msg_id: int) -> Optional[MessageMapping]:
        """Get mapping by main message ID from SQL"""
        return self.repo.get(main_msg_id)

    def get_mapping_by_trade(self, trade_id: str) -> Optional[MessageMapping]:
        """Get mapping by trade ID from SQL"""
        return self.repo.get_by_trade_id(trade_id)

    def get_thread_parent(self, mapping: MessageMapping) -> Optional[MessageMapping]:
        """Get parent mapping in thread from SQL"""
        if mapping.parent_main_msg_id:
            return self.repo.get(mapping.parent_main_msg_id)
        return None

    def get_thread_children(self, main_msg_id: int) -> List[MessageMapping]:
        """Get all child mappings in thread from SQL"""
        return self.repo.get_children(main_msg_id)

    def update_mapping(
        self,
        main_msg_id: int,
        **updates
    ) -> Optional[MessageMapping]:
        """Update existing mapping in SQL"""
        mapping = self.repo.get(main_msg_id)
        if not mapping:
            return None

        for key, value in updates.items():
            if hasattr(mapping, key):
                setattr(mapping, key, value)

        self.repo.save(mapping)
        return mapping

    def add_tg_message(self, main_msg_id: int, tg_msg_id: int) -> bool:
        """Add Telegram message ID to mapping in SQL"""
        mapping = self.repo.get(main_msg_id)
        if not mapping:
            return False

        if tg_msg_id not in mapping.tg_msg_ids:
            mapping.tg_msg_ids.append(tg_msg_id)
            self.repo.save(mapping)

        return True

    def set_twitter_id(
        self,
        main_msg_id: int,
        tweet_id: str,
        account: str
    ) -> bool:
        """Set Twitter ID for mapping in SQL"""
        mapping = self.repo.get(main_msg_id)
        if not mapping:
            return False

        mapping.twitter = {
            'tweet_id': tweet_id,
            'account': account
        }
        self.repo.save(mapping)
        return True

    def resolve_parent_for_reply(
        self,
        is_admin_channel: bool,
        reply_to_msg_id: Optional[int] = None,
        trade_id: Optional[str] = None
    ) -> Optional[MessageMapping]:
        """Resolve parent mapping for reply from SQL (FIX 6)"""
        # If replying to specific message
        if reply_to_msg_id:
            parent = self.repo.get(reply_to_msg_id)
            if parent:
                return parent

        # If trade ID provided, find root mapping
        if trade_id:
            mapping = self.repo.get_by_trade_id(trade_id)
            if mapping:
                # Find root of thread
                while mapping.parent_main_msg_id:
                    parent = self.repo.get(mapping.parent_main_msg_id)
                    if parent:
                        mapping = parent
                    else:
                        break
                return mapping

        return None

    def get_nesting_level(self, mapping: MessageMapping) -> int:
        """Get nesting level in thread hierarchy"""
        level = 0
        current = mapping

        while current.parent_main_msg_id:
            level += 1
            parent = self.repo.get(current.parent_main_msg_id)
            if not parent:
                break
            current = parent

            if level >= self.cfg.hierarchy_levels:
                break

        return level

    def should_create_thread(self, message_type: str) -> bool:
        """Check if message type should create thread"""
        rules = self.cfg.rules.get(message_type, {})
        return rules.get('create_thread', False)

    def should_reply_to_parent(self, message_type: str) -> bool:
        """Check if message type should reply to parent"""
        rules = self.cfg.rules.get(message_type, {})
        return rules.get('reply_to_parent', False)

    def get_reply_behavior(self, channel_type: str) -> Dict[str, Any]:
        """Get reply behavior for channel type"""
        if channel_type == 'admin':
            return self.cfg.admin_channel_behavior
        elif channel_type == 'target':
            return self.cfg.target_channel_behavior
        elif channel_type == 'twitter':
            return self.cfg.twitter_behavior
        return {}

    def build_thread_chain(self, main_msg_id: int) -> List[MessageMapping]:
        """Build complete thread chain from root to message from SQL"""
        chain = []
        current = self.repo.get(main_msg_id)

        while current:
            chain.insert(0, current)
            if current.parent_main_msg_id:
                current = self.repo.get(current.parent_main_msg_id)
            else:
                break

        return chain

    def delete_mapping(self, main_msg_id: int) -> bool:
        """Delete mapping by ID from SQL"""
        return self.repo.delete(main_msg_id)

    def get_all_mappings(self) -> List[MessageMapping]:
        """Get all mappings from SQL"""
        return self.repo.get_all()

# Singleton
_mapping_service: Optional[MessageMappingService] = None

def get_mapping_service() -> MessageMappingService:
    global _mapping_service
    if _mapping_service is None:
        _mapping_service = MessageMappingService()
    return _mapping_service

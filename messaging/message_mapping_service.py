"""
Message Mapping Service

Links trade_id ↔ message_id for threading and reply chains.
Integrates with MessageMapping table.

Responsibilities:
- Save message_id, chat_id, platform
- Link trade_id ↔ message_id
- Store parent_message_id for threading
- Retrieve latest message for a trade
- Support reply chains with nested replies
- Support multiple message IDs per trade
- Platform separation
"""

import logging
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import select, desc, and_
from sqlalchemy.orm import Session

from core.db import MessageMappingModel, Database

logger = logging.getLogger(__name__)


class MessageMappingService:
    """Service for managing message-to-trade mappings with full chain support."""

    def __init__(self, db: Database):
        self.db = db
        logger.info("MessageMappingService initialized")

    @contextmanager
    def _session(self):
        session = self.db.get_session()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def save_mapping(
        self,
        trade_id: int,
        platform: str,
        message_id: str,
        channel_id: Optional[str],
        message_type: str,
        parent_tg_msg_id: Optional[str] = None,
        parent_main_msg_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None
    ) -> None:
        """Save a new message mapping with full chain support.

        Args:
            trade_id: Internal trade ID
            platform: Platform name (telegram, twitter, etc.)
            message_id: Platform-specific message ID
            channel_id: Channel/chat ID
            message_type: Type of message (trade_setup, partial_close_specific, etc.)
            parent_tg_msg_id: Parent Telegram message ID for threading
            parent_main_msg_id: Parent main message ID (cross-platform)
            reply_to_message_id: Direct reply target message ID
        """
        with self._session() as session:
            mapping = MessageMappingModel(
                trade_id=trade_id,
                platform=platform,
                message_id=message_id,
                channel_id=channel_id,
                message_type=message_type,
                parent_tg_msg_id=parent_tg_msg_id,
                parent_main_msg_id=parent_main_msg_id,
                reply_to_message_id=reply_to_message_id,
                created_at=datetime.utcnow()
            )
            session.add(mapping)
            logger.info(f"Saved mapping: trade={trade_id}, msg={message_id}, platform={platform}, type={message_type}")

    def get_latest_message(
        self,
        trade_id: int,
        platform: str,
        message_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the latest message for a trade on a platform."""
        with self._session() as session:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.trade_id == trade_id,
                MessageMappingModel.platform == platform
            )

            if message_type:
                stmt = stmt.where(MessageMappingModel.message_type == message_type)

            stmt = stmt.order_by(desc(MessageMappingModel.created_at))
            result = session.execute(stmt).scalars().first()

            if result:
                return self._mapping_to_dict(result)
            return None

    def get_message_chain(
        self,
        trade_id: int,
        platform: str
    ) -> List[Dict[str, Any]]:
        """Get all messages for a trade in chronological order."""
        with self._session() as session:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.trade_id == trade_id,
                MessageMappingModel.platform == platform
            ).order_by(MessageMappingModel.created_at)

            results = session.execute(stmt).scalars().all()
            return [self._mapping_to_dict(r) for r in results]

    def get_all_messages_for_trade(
        self,
        trade_id: int
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get all messages across all platforms for a trade."""
        with self._session() as session:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.trade_id == trade_id
            ).order_by(MessageMappingModel.created_at)

            results = session.execute(stmt).scalars().all()

            # Group by platform
            by_platform = {}
            for r in results:
                if r.platform not in by_platform:
                    by_platform[r.platform] = []
                by_platform[r.platform].append(self._mapping_to_dict(r))

            return by_platform

    def get_parent_message_id(
        self,
        trade_id: int,
        platform: str,
        message_type: Optional[str] = None
    ) -> Optional[str]:
        """Get the root message ID to reply to (legacy - returns first message)."""
        with self._session() as session:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.trade_id == trade_id,
                MessageMappingModel.platform == platform
            ).order_by(MessageMappingModel.created_at)

            result = session.execute(stmt).scalars().first()
            return result.message_id if result else None

    def resolve_reply_parent(
        self,
        trade_id: int,
        platform: str,
        message_type: str,
        reply_to_msg_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Resolve correct parent message for nested replies.

        Logic:
        1. If reply_to_msg_id provided, find that message and use its chain
        2. Otherwise find the most recent message of the same type or the root
        3. Return full parent info for proper threading
        """
        with self._session() as session:
            if reply_to_msg_id:
                # Find the specific message being replied to
                stmt = select(MessageMappingModel).where(
                    MessageMappingModel.trade_id == trade_id,
                    MessageMappingModel.platform == platform,
                    MessageMappingModel.message_id == reply_to_msg_id
                )
                result = session.execute(stmt).scalars().first()
                if result:
                    return self._mapping_to_dict(result)

            # Find the most recent message for this trade/platform
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.trade_id == trade_id,
                MessageMappingModel.platform == platform
            ).order_by(desc(MessageMappingModel.created_at))

            result = session.execute(stmt).scalars().first()
            if result:
                return self._mapping_to_dict(result)

            return None

    def get_trade_by_message(
        self,
        platform: str,
        message_id: str,
        channel_id: Optional[str] = None
    ) -> Optional[int]:
        """Get trade_id by message_id (for reply handling)."""
        with self._session() as session:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.platform == platform,
                MessageMappingModel.message_id == message_id
            )
            if channel_id:
                stmt = stmt.where(MessageMappingModel.channel_id == channel_id)

            result = session.execute(stmt).scalars().first()
            return result.trade_id if result else None

    def get_message_by_id(
        self,
        platform: str,
        message_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get full message mapping by platform message ID."""
        with self._session() as session:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.platform == platform,
                MessageMappingModel.message_id == message_id
            )
            result = session.execute(stmt).scalars().first()
            return self._mapping_to_dict(result) if result else None

    def _mapping_to_dict(self, mapping: MessageMappingModel) -> Dict[str, Any]:
        """Convert mapping model to dictionary."""
        return {
            "id": mapping.id,
            "trade_id": mapping.trade_id,
            "platform": mapping.platform,
            "message_id": mapping.message_id,
            "channel_id": mapping.channel_id,
            "message_type": mapping.message_type,
            "parent_tg_msg_id": mapping.parent_tg_msg_id,
            "parent_main_msg_id": mapping.parent_main_msg_id,
            "reply_to_message_id": mapping.reply_to_message_id,
            "created_at": mapping.created_at.isoformat() if mapping.created_at else None
        }

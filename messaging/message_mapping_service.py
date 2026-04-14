"""
Message Mapping Service

Links trade_id ↔ message_id for threading and reply chains.
Integrates with MessageMapping table.

Responsibilities:
- Save message_id, chat_id, platform
- Link trade_id ↔ message_id
- Store parent_message_id for threading
- Retrieve latest message for a trade
- Support reply chains
"""

import logging
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from core.db import MessageMappingModel, Database

logger = logging.getLogger(__name__)

class MessageMappingService:
    """Service for managing message-to-trade mappings"""

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
        parent_message_id: Optional[str] = None
    ) -> None:
        """Save a new message mapping"""
        with self._session() as session:
            mapping = MessageMappingModel(
                trade_id=trade_id,
                platform=platform,
                message_id=message_id,
                channel_id=channel_id,
                parent_message_id=parent_message_id,
                message_type=message_type
            )
            session.add(mapping)
            logger.info(f"Saved mapping: trade={trade_id}, msg={message_id}, platform={platform}")

    def get_latest_message(
        self,
        trade_id: int,
        platform: str,
        message_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the latest message for a trade on a platform"""
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
                return {
                    "id": result.id,
                    "trade_id": result.trade_id,
                    "platform": result.platform,
                    "message_id": result.message_id,
                    "channel_id": result.channel_id,
                    "parent_message_id": result.parent_message_id,
                    "message_type": result.message_type,
                    "created_at": result.created_at
                }
            return None

    def get_message_chain(
        self,
        trade_id: int,
        platform: str
    ) -> List[Dict[str, Any]]:
        """Get all messages for a trade in chronological order"""
        with self._session() as session:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.trade_id == trade_id,
                MessageMappingModel.platform == platform
            ).order_by(MessageMappingModel.created_at)

            results = session.execute(stmt).scalars().all()

            return [
                {
                    "id": r.id,
                    "message_id": r.message_id,
                    "parent_message_id": r.parent_message_id,
                    "message_type": r.message_type,
                    "created_at": r.created_at
                }
                for r in results
            ]

    def get_parent_message_id(
        self,
        trade_id: int,
        platform: str
    ) -> Optional[str]:
        """Get the root message ID to reply to"""
        # Get the first message (trade setup) as parent
        with self._session() as session:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.trade_id == trade_id,
                MessageMappingModel.platform == platform
            ).order_by(MessageMappingModel.created_at)

            result = session.execute(stmt).scalars().first()
            return result.message_id if result else None

    def get_trade_by_message(
        self,
        platform: str,
        message_id: str,
        channel_id: Optional[str] = None
    ) -> Optional[int]:
        """Get trade_id by message_id (for reply handling)"""
        with self._session() as session:
            stmt = select(MessageMappingModel).where(
                MessageMappingModel.platform == platform,
                MessageMappingModel.message_id == message_id
            )
            if channel_id:
                stmt = stmt.where(MessageMappingModel.channel_id == channel_id)

            result = session.execute(stmt).scalars().first()
            return result.trade_id if result else None

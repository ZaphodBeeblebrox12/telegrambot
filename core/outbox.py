"""Outbox Pattern for Reliable Async Processing - SQL-based with Transaction Support + Twitter Controls

FIXED: Twitter filtering now happens at ENQUEUE time, not process time.
This prevents wasted processing on filtered messages.
"""
import json
import asyncio
import uuid
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from sqlalchemy.orm import Session
from core.db import Database, OutboxMessageModel
from core.twitter_toggle_manager import is_twitter_enabled
from core.twitter_style_manager import should_post_to_twitter

class OutboxStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"

@dataclass
class OutboxMessage:
    id: str
    destination: str
    channel_id: Optional[str]
    message_type: str
    payload: Dict[str, Any]
    status: OutboxStatus = OutboxStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())
    processed_at: Optional[float] = None
    error: Optional[str] = None


class TransactionalOutbox:
    """Transactional outbox that participates in DB transactions (FIX 3)"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def _should_skip_twitter_enqueue(self, destination: str, message_type: str) -> bool:
        """
        Check if Twitter message should be skipped BEFORE enqueuing.
        Returns True if message should NOT be enqueued.

        FIXED: This is now called at ENQUEUE time, not process time.
        """
        if destination != "twitter":
            return False

        # Check global Twitter toggle
        if not is_twitter_enabled():
            print(f"[TWITTER] Skipping enqueue for {message_type}: Twitter disabled")
            return True

        # Check event type filter
        event_type = message_type or "position_update"
        if not should_post_to_twitter(event_type):
            print(f"[TWITTER] Skipping enqueue for {event_type}: not in allowed list")
            return True

        return False

    def enqueue_in_transaction(
        self,
        session: Session,
        destination: str,
        message_type: str,
        payload: Dict[str, Any],
        channel_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Enqueue message within an existing transaction.
        CRITICAL: Uses provided session for transactional consistency.

        FIXED: Returns None if message is filtered out (Twitter checks).
        This prevents wasted processing on filtered messages.
        """
        # TWITTER FILTERING AT ENQUEUE TIME (before DB write)
        if self._should_skip_twitter_enqueue(destination, message_type):
            return None  # Signal that message was filtered out

        msg_id = str(uuid.uuid4())[:8]

        msg_model = OutboxMessageModel(
            message_id=msg_id,
            destination=destination,
            channel_id=channel_id,
            message_type=message_type,
            payload=json.dumps(payload),
            status="pending"
        )
        session.add(msg_model)

        return msg_id

    def get_pending(self, session: Optional[Session] = None, limit: int = 100) -> List[OutboxMessageModel]:
        """Get pending messages"""
        if session is None:
            session = self.db.get_session()
            close_after = True
        else:
            close_after = False

        try:
            return session.query(OutboxMessageModel).filter(
                OutboxMessageModel.status.in_(['pending', 'retrying'])
            ).limit(limit).all()
        finally:
            if close_after:
                session.close()

    def mark_processed(
        self,
        session: Session,
        message_id: str,
        status: str,
        error: Optional[str] = None
    ):
        """Mark message as processed within transaction"""
        msg = session.query(OutboxMessageModel).filter_by(message_id=message_id).first()
        if msg:
            msg.status = status
            msg.error = error
            if status == "completed":
                msg.processed_at = datetime.utcnow()


class AsyncProcessor:
    """Async message processor with retry (Twitter filtering now in enqueue)"""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.handlers: Dict[str, callable] = {}

    def register_handler(self, destination: str, handler: callable):
        self.handlers[destination] = handler

    async def process_with_retry(
        self,
        message: OutboxMessageModel
    ) -> bool:
        """
        Process message with retry.
        Note: Twitter filtering now happens at enqueue time, not here.
        """
        handler = self.handlers.get(message.destination)
        if not handler:
            message.error = f"No handler for destination: {message.destination}"
            message.status = "failed"
            return False

        payload = json.loads(message.payload) if message.payload else {}

        for attempt in range(message.retry_count, self.max_retries + 1):
            try:
                message.status = "processing"
                message.retry_count = attempt

                await handler(payload)

                message.status = "completed"
                message.processed_at = datetime.utcnow()
                message.error = None
                return True

            except Exception as e:
                message.error = str(e)[:500]

                if attempt < self.max_retries:
                    message.status = "retrying"
                    delay = min(2 ** attempt, 60)
                    await asyncio.sleep(delay)
                else:
                    message.status = "failed"

        return False


class OutboxManager:
    """Main outbox manager with transactional support (FIX 3) + Twitter controls at enqueue"""

    def __init__(self):
        self.db = Database()
        self.outbox = TransactionalOutbox(self.db)
        self.processor = AsyncProcessor()
        self._running = False

    def register_handler(self, destination: str, handler: callable):
        self.processor.register_handler(destination, handler)

    def enqueue_in_transaction(
        self,
        session: Session,
        destination: str,
        message_type: str,
        payload: Dict[str, Any],
        channel_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Enqueue within existing transaction (FIX 3).

        FIXED: Returns None if message was filtered out (e.g., by Twitter settings).
        Caller should check for None and handle appropriately.
        """
        return self.outbox.enqueue_in_transaction(
            session, destination, message_type, payload, channel_id
        )

    async def process_pending(self):
        """Process all pending messages"""
        session = self.db.get_session()
        try:
            pending = self.outbox.get_pending(session)

            for msg_model in pending:
                success = await self.processor.process_with_retry(msg_model)
                self.outbox.mark_processed(
                    session, msg_model.message_id,
                    msg_model.status, msg_model.error
                )
                session.commit()

                if not success:
                    print(f"Failed to process message {msg_model.message_id}: {msg_model.error}")
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    async def start_processor(self, interval: float = 5.0):
        self._running = True
        while self._running:
            try:
                await self.process_pending()
            except Exception as e:
                print(f"Outbox processor error: {e}")
            await asyncio.sleep(interval)

    def stop_processor(self):
        self._running = False

    async def run_once(self):
        await self.process_pending()


_outbox: Optional[OutboxManager] = None

def get_outbox() -> OutboxManager:
    global _outbox
    if _outbox is None:
        _outbox = OutboxManager()
    return _outbox

"""Outbox Pattern for Reliable Async Processing - SQL-based with Transaction Support"""
import json
import asyncio
import uuid
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from sqlalchemy.orm import Session
from core.db import Database, OutboxMessageModel

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
    """Transactional outbox that participates in DB transactions (FIX 2)"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def enqueue_in_transaction(
        self,
        session: Session,
        destination: str,
        message_type: str,
        payload: Dict[str, Any],
        channel_id: Optional[str] = None
    ) -> str:
        """
        Enqueue message within an existing transaction.
        CRITICAL FIX: Uses provided session for transactional consistency.
        """
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
    """Async message processor with retry"""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.handlers: Dict[str, callable] = {}

    def register_handler(self, destination: str, handler: callable):
        self.handlers[destination] = handler

    async def process_with_retry(
        self,
        message: OutboxMessageModel
    ) -> bool:
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
    """Main outbox manager with transactional support (FIX 2)"""

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
    ) -> str:
        """Enqueue within existing transaction (FIX 2)"""
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

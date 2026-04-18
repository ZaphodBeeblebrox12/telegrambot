"""Outbox Pattern for Reliable Async Processing - SQL-based with Transaction Support + Twitter Controls
FIXED: Twitter filtering now happens at ENQUEUE time, not process time.
FIXED: Atomic message claiming prevents duplicate sends under concurrency.
FIXED: Detached instance error resolved by copying data before session close.
"""
import json
import asyncio
import uuid
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import text
from core.db import Database, OutboxMessageModel
from core.twitter_toggle_manager import is_twitter_enabled
from core.twitter_style_manager import should_post_to_twitter

logger = logging.getLogger(__name__)

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

        # JSON SAFETY: Verify payload is serializable before writing to DB
        try:
            payload_json = json.dumps(payload)
        except TypeError as e:
            logger.error(f"Outbox payload not JSON serializable: {e}. Payload keys: {list(payload.keys())}")
            raise ValueError(f"Outbox payload contains non-serializable data: {e}") from e

        msg_id = str(uuid.uuid4())[:8]

        msg_model = OutboxMessageModel(
            message_id=msg_id,
            destination=destination,
            channel_id=channel_id,
            message_type=message_type,
            payload=payload_json,
            status="pending"
        )
        session.add(msg_model)

        return msg_id

    def get_pending(self, session: Optional[Session] = None, limit: int = 100) -> List[OutboxMessageModel]:
        """Get pending messages."""
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
        """
        Process pending messages with atomic claiming.

        CRITICAL FIX: Each message is atomically claimed before sending.
        Pattern:
        1. Read next pending message
        2. Atomically UPDATE status='processing' WHERE status='pending'
        3. COMMIT claim immediately
        4. Copy all data from model before closing session (prevents detached instance error)
        5. Only if rowcount==1 (claim succeeded), send message using copied data
        6. Update final status in separate transaction

        This prevents duplicate sends when multiple workers run concurrently.
        """
        while True:
            # Step 1 & 2: Read and atomically claim next pending message
            session = self.db.get_session()
            claimed_msg_data = None
            try:
                # Read next pending message
                msg_model = session.query(OutboxMessageModel).filter(
                    OutboxMessageModel.status == 'pending'
                ).order_by(OutboxMessageModel.created_at.asc()).first()

                if not msg_model:
                    break

                # CRITICAL FIX: Copy ALL data we need BEFORE any session operation
                # that might require lazy loading. We must extract everything here
                # while the object is still attached to the session.
                msg_id = msg_model.id
                message_id = msg_model.message_id
                destination = msg_model.destination
                channel_id = msg_model.channel_id
                message_type = msg_model.message_type
                payload = msg_model.payload
                retry_count = msg_model.retry_count
                max_retries = msg_model.max_retries

                # ATOMIC CLAIM: update status only if still pending
                result = session.execute(
                    text("""
                        UPDATE outbox_messages 
                        SET status = 'processing'
                        WHERE id = :id AND status = 'pending'
                    """),
                    {"id": msg_id}
                )

                # Check if claim succeeded
                if result.rowcount == 0:
                    # Another worker claimed it, skip and try next
                    continue

                # COMMIT CLAIM IMMEDIATELY
                session.commit()

                # Store copied data for processing outside session
                claimed_msg_data = {
                    'message_id': message_id,
                    'destination': destination,
                    'channel_id': channel_id,
                    'message_type': message_type,
                    'payload': payload,
                    'retry_count': retry_count,
                    'max_retries': max_retries,
                }

            except Exception as e:
                session.rollback()
                logger.error(f"Outbox claim error: {e}")
                break
            finally:
                session.close()

            # Step 5: Send message using copied data (outside any session)
            if claimed_msg_data:
                try:
                    # Create a detached model-like object with all data pre-loaded
                    # We create a simple object that has the attributes AsyncProcessor expects
                    class DetachedMessage:
                        def __init__(self, data):
                            self.message_id = data['message_id']
                            self.destination = data['destination']
                            self.channel_id = data['channel_id']
                            self.message_type = data['message_type']
                            self.payload = data['payload']
                            self.retry_count = data['retry_count']
                            self.max_retries = data['max_retries']
                            self.status = 'processing'
                            self.error = None
                            self.processed_at = None

                    detached_msg = DetachedMessage(claimed_msg_data)
                    success = await self.processor.process_with_retry(detached_msg)

                    # Step 6: Mark final status in new transaction
                    session2 = self.db.get_session()
                    try:
                        self.outbox.mark_processed(
                            session2, detached_msg.message_id,
                            'completed' if success else 'failed',
                            detached_msg.error if not success else None
                        )
                        session2.commit()
                    except Exception as e:
                        session2.rollback()
                        logger.error(f"Failed to mark message processed: {e}")
                    finally:
                        session2.close()

                except Exception as e:
                    logger.error(f"Message send failed: {e}")

                    # Mark as failed
                    session2 = self.db.get_session()
                    try:
                        self.outbox.mark_processed(
                            session2, claimed_msg_data['message_id'], 
                            'failed', str(e)
                        )
                        session2.commit()
                    except Exception as e2:
                        session2.rollback()
                    finally:
                        session2.close()

    async def start_processor(self, interval: float = 5.0):
        self._running = True
        while self._running:
            try:
                await self.process_pending()
            except Exception as e:
                logger.error(f"Outbox processor error: {e}")
            # Sleep unconditionally to prevent CPU spinning
            await asyncio.sleep(interval)

    def stop_processor(self):
        self._running = False

    async def run_once(self):
        """Process pending messages once."""
        await self.process_pending()


_outbox: Optional[OutboxManager] = None


def get_outbox() -> OutboxManager:
    global _outbox
    if _outbox is None:
        _outbox = OutboxManager()
    return _outbox

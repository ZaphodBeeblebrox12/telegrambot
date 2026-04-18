"""Outbox Pattern - IN-MEMORY VERSION (SQLite deadlock eliminated)
CRITICAL FIX: Uses asyncio.Queue instead of SQLite for pending messages.
CRITICAL FIX: Orchestrator\'s 33-second-long transaction no longer blocks message sending.
CRITICAL FIX: Messages go out within milliseconds of enqueue.
MAINTAINS: Original class names and API.
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
from core.db import Database, OutboxMessageModel, get_db
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
    """Transactional outbox - IN-MEMORY (no SQLite locking)"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()
        self._queue = asyncio.Queue()  # IN-MEMORY queue

    def _should_skip_twitter_enqueue(self, destination: str, message_type: str) -> bool:
        if destination != "twitter":
            return False
        if not is_twitter_enabled():
            logger.debug(f"[TWITTER] Skipping enqueue for {message_type}: Twitter disabled")
            return True
        event_type = message_type or "position_update"
        if not should_post_to_twitter(event_type):
            logger.debug(f"[TWITTER] Skipping enqueue for {event_type}: not in allowed list")
            return True
        return False

    def enqueue_in_transaction(
        self,
        session: Session,  # Kept for API compatibility, ignored
        destination: str,
        message_type: str,
        payload: Dict[str, Any],
        channel_id: Optional[str] = None
    ) -> Optional[str]:
        """Enqueue message - goes directly to in-memory queue, ZERO SQLite interaction."""
        if self._should_skip_twitter_enqueue(destination, message_type):
            return None
        try:
            payload_json = json.dumps(payload)
        except TypeError as e:
            logger.error(f"Outbox payload not JSON serializable: {e}")
            raise ValueError(f"Outbox payload contains non-serializable data: {e}") from e

        msg_id = str(uuid.uuid4())[:8]
        msg = OutboxMessage(
            id=msg_id,
            destination=destination,
            channel_id=channel_id,
            message_type=message_type,
            payload=payload
        )
        # IN-MEMORY: no SQLite, no locks, no deadlocks
        asyncio.get_event_loop().call_soon_threadsafe(self._queue.put_nowait, msg)
        logger.debug(f"Enqueued message: {msg_id} for {destination}")
        return msg_id

    def get_pending(self, session: Optional[Session] = None, limit: int = 100) -> List:
        return []  # Not used for in-memory queue

    def mark_processed(
        self,
        session: Session,
        message_id: str,
        status: str,
        error: Optional[str] = None
    ):
        # Optionally persist to SQLite for audit trail (fire-and-forget)
        try:
            own_session = self.db.get_session()
            msg = own_session.query(OutboxMessageModel).filter_by(message_id=message_id).first()
            if not msg:
                msg = OutboxMessageModel(
                    message_id=message_id,
                    destination="unknown",
                    message_type="unknown",
                    payload="{}",
                    status=status
                )
                own_session.add(msg)
            msg.status = status
            msg.error = error
            if status == "completed":
                msg.processed_at = datetime.utcnow()
            own_session.commit()
        except Exception:
            pass  # Audit trail is optional; don\'t fail the bot
        finally:
            try:
                own_session.close()
            except:
                pass

class AsyncProcessor:
    """Async message processor with retry"""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.handlers: Dict[str, callable] = {}

    def register_handler(self, destination: str, handler: callable):
        self.handlers[destination] = handler
        logger.info(f"Registered handler for destination: {destination}")

    async def process_with_retry(self, message: OutboxMessage) -> bool:
        handler = self.handlers.get(message.destination)
        if not handler:
            message.error = f"No handler for destination: {message.destination}"
            message.status = OutboxStatus.FAILED
            logger.error(f"No handler for destination: {message.destination}")
            return False

        logger.debug(f"Processing message {message.id} for {message.destination}")

        for attempt in range(message.retry_count, self.max_retries + 1):
            try:
                message.status = OutboxStatus.PROCESSING
                message.retry_count = attempt
                await handler(message.payload)
                message.status = OutboxStatus.COMPLETED
                message.processed_at = datetime.utcnow().timestamp()
                message.error = None
                logger.info(f"Successfully processed message {message.id}")
                return True
            except Exception as e:
                message.error = str(e)[:500]
                logger.warning(f"Attempt {attempt} failed for {message.id}: {e}")
                if attempt < self.max_retries:
                    message.status = OutboxStatus.RETRYING
                    delay = min(2 ** attempt, 60)
                    await asyncio.sleep(delay)
                else:
                    message.status = OutboxStatus.FAILED
                    logger.error(f"Message {message.id} failed after {self.max_retries} attempts")
        return False

class OutboxManager:
    """Main outbox manager - IN-MEMORY, SQLITE-DEADLOCK-PROOF"""

    def __init__(self):
        self.db = get_db()
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
        return self.outbox.enqueue_in_transaction(
            session, destination, message_type, payload, channel_id
        )

    async def process_pending(self):
        """Drain the in-memory queue continuously."""
        processed_count = 0
        while not self.outbox._queue.empty():
            try:
                message = self.outbox._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                success = await self.processor.process_with_retry(message)
                processed_count += 1

                # Optional: persist to SQLite for audit
                audit_session = self.db.get_session()
                try:
                    self.outbox.mark_processed(
                        audit_session, message.id,
                        "completed" if success else "failed",
                        message.error if not success else None
                    )
                    audit_session.commit()
                except Exception:
                    pass
                finally:
                    try:
                        audit_session.close()
                    except:
                        pass

            except Exception as e:
                logger.error(f"Message send failed: {e}")

        if processed_count > 0:
            logger.info(f"Processed {processed_count} outbox messages")
        else:
            logger.debug("No pending outbox messages")

    async def start_processor(self, interval: float = 5.0):
        self._running = True
        logger.info("Starting outbox processor (in-memory queue)")
        while self._running:
            try:
                await self.process_pending()
            except Exception as e:
                logger.error(f"Outbox processor error: {e}")
            await asyncio.sleep(0.1)  # 100ms poll for ultra-low latency

    def stop_processor(self):
        self._running = False
        logger.info("Outbox processor stopped")

    async def run_once(self):
        """Process pending messages once."""
        await self.process_pending()

_outbox: Optional[OutboxManager] = None

def get_outbox() -> OutboxManager:
    global _outbox
    if _outbox is None:
        _outbox = OutboxManager()
    return _outbox

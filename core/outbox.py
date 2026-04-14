"""Outbox Pattern for Reliable Async Processing"""
import json
import asyncio
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from enum import Enum
import aiohttp


class OutboxStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class OutboxMessage:
    id: str
    destination: str  # telegram, twitter
    channel_id: Optional[str]
    message_type: str
    payload: Dict[str, Any]
    status: OutboxStatus = OutboxStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())
    processed_at: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'destination': self.destination,
            'channel_id': self.channel_id,
            'message_type': self.message_type,
            'payload': self.payload,
            'status': self.status.value,
            'retry_count': self.retry_count,
            'max_retries': self.max_retries,
            'created_at': self.created_at,
            'processed_at': self.processed_at,
            'error': self.error
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OutboxMessage':
        return cls(
            id=data['id'],
            destination=data['destination'],
            channel_id=data.get('channel_id'),
            message_type=data['message_type'],
            payload=data['payload'],
            status=OutboxStatus(data.get('status', 'pending')),
            retry_count=data.get('retry_count', 0),
            max_retries=data.get('max_retries', 3),
            created_at=data.get('created_at', datetime.now().timestamp()),
            processed_at=data.get('processed_at'),
            error=data.get('error')
        )


class OutboxStore:
    """JSON-based outbox storage"""

    def __init__(self, file_path: str = 'outbox.json'):
        self.file_path = Path(file_path)
        self._ensure_file()

    def _ensure_file(self):
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, 'w') as f:
                json.dump({}, f)

    def _load_all(self) -> Dict[str, Any]:
        try:
            with open(self.file_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_all(self, data: Dict[str, Any]):
        with open(self.file_path, 'w') as f:
            json.dump(data, f, indent=2)

    def save(self, message: OutboxMessage):
        data = self._load_all()
        data[message.id] = message.to_dict()
        self._save_all(data)

    def get(self, msg_id: str) -> Optional[OutboxMessage]:
        data = self._load_all()
        if msg_id in data:
            return OutboxMessage.from_dict(data[msg_id])
        return None

    def get_pending(self, limit: int = 100) -> List[OutboxMessage]:
        data = self._load_all()
        pending = [
            OutboxMessage.from_dict(m) 
            for m in data.values() 
            if m['status'] in ['pending', 'retrying']
        ]
        return pending[:limit]

    def get_failed(self) -> List[OutboxMessage]:
        data = self._load_all()
        return [
            OutboxMessage.from_dict(m) 
            for m in data.values() 
            if m['status'] == 'failed'
        ]


class RetryPolicy:
    """Configurable retry policy"""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base

    def get_delay(self, attempt: int) -> float:
        """Get delay for retry attempt"""
        import random
        delay = self.base_delay * (self.exponential_base ** attempt)
        jitter = random.uniform(0, 0.1 * delay)
        return min(delay + jitter, self.max_delay)


class AsyncProcessor:
    """Async message processor with retry"""

    def __init__(self, retry_policy: Optional[RetryPolicy] = None):
        self.retry_policy = retry_policy or RetryPolicy()
        self.handlers: Dict[str, callable] = {}

    def register_handler(self, destination: str, handler: callable):
        """Register handler for destination"""
        self.handlers[destination] = handler

    async def process_with_retry(
        self,
        message: OutboxMessage
    ) -> bool:
        """Process message with retry logic"""
        handler = self.handlers.get(message.destination)
        if not handler:
            message.error = f"No handler for destination: {message.destination}"
            message.status = OutboxStatus.FAILED
            return False

        for attempt in range(message.retry_count, self.retry_policy.max_retries + 1):
            try:
                message.status = OutboxStatus.PROCESSING
                message.retry_count = attempt

                # Execute handler
                await handler(message.payload)

                message.status = OutboxStatus.COMPLETED
                message.processed_at = datetime.now().timestamp()
                message.error = None
                return True

            except Exception as e:
                message.error = str(e)

                if attempt < self.retry_policy.max_retries:
                    message.status = OutboxStatus.RETRYING
                    delay = self.retry_policy.get_delay(attempt)
                    await asyncio.sleep(delay)
                else:
                    message.status = OutboxStatus.FAILED

        return False


class OutboxManager:
    """Main outbox manager"""

    def __init__(self):
        self.store = OutboxStore()
        self.processor = AsyncProcessor()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def register_handler(self, destination: str, handler: callable):
        """Register destination handler"""
        self.processor.register_handler(destination, handler)

    async def enqueue(
        self,
        destination: str,
        message_type: str,
        payload: Dict[str, Any],
        channel_id: Optional[str] = None
    ) -> str:
        """Add message to outbox"""
        import uuid
        msg_id = str(uuid.uuid4())[:8]  # Short ID for outbox only

        message = OutboxMessage(
            id=msg_id,
            destination=destination,
            channel_id=channel_id,
            message_type=message_type,
            payload=payload
        )

        self.store.save(message)
        return msg_id

    async def process_pending(self):
        """Process all pending messages"""
        pending = self.store.get_pending()

        for message in pending:
            success = await self.processor.process_with_retry(message)
            self.store.save(message)

            if not success:
                print(f"Failed to process message {message.id}: {message.error}")

    async def start_processor(self, interval: float = 5.0):
        """Start background processor"""
        self._running = True

        while self._running:
            try:
                await self.process_pending()
            except Exception as e:
                print(f"Outbox processor error: {e}")

            await asyncio.sleep(interval)

    def stop_processor(self):
        """Stop background processor"""
        self._running = False

    async def run_once(self):
        """Run one processing cycle"""
        await self.process_pending()


# Singleton
_outbox: Optional[OutboxManager] = None

def get_outbox() -> OutboxManager:
    global _outbox
    if _outbox is None:
        _outbox = OutboxManager()
    return _outbox

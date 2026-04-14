"""
Domain Models - Pure Python dataclasses
"""

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    NOT_TRIGGERED = "NOT_TRIGGERED"

class EntryType(Enum):
    INITIAL = "INITIAL"
    PYRAMID = "PYRAMID"

class EventType(Enum):
    TRADE_CREATED = "TRADE_CREATED"
    STOP_UPDATED = "STOP_UPDATED"
    TARGET_UPDATED = "TARGET_UPDATED"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    FULL_CLOSE = "FULL_CLOSE"
    PYRAMID_ADDED = "PYRAMID_ADDED"
    TRADE_CANCELLED = "TRADE_CANCELLED"

@dataclass
class TradeEntry:
    entry_price: Decimal
    size: Decimal
    entry_type: EntryType
    sequence: int = 0
    closed_size: Decimal = field(default=Decimal("0"))
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    @property
    def remaining_size(self) -> Decimal:
        return self.size - self.closed_size

    @property
    def is_fully_closed(self) -> bool:
        return self.remaining_size <= 0

@dataclass
class Trade:
    symbol: str
    side: str
    asset_class: str
    entries: List[TradeEntry] = field(default_factory=list)
    status: TradeStatus = field(default=TradeStatus.OPEN)
    trade_id: Optional[str] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def total_size(self) -> Decimal:
        return sum((e.size for e in self.entries), Decimal("0"))

    @property
    def remaining_size(self) -> Decimal:
        return sum((e.remaining_size for e in his.entries), Decimal("0"))

    @property
    def is_closed(self) -> bool:
        return self.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED, TradeStatus.NOT_TRIGGERED)

@dataclass
class TradeEvent:
    event_type: EventType
    payload: Dict[str, Any]
    sequence: int = 0
    id: Optional[int] = None
    idempotency_key: Optional[str] = None
    created_at: Optional[datetime] = None

@dataclass
class TradeSnapshot:
    weighted_avg_entry: Decimal = Decimal("0")
    total_size: Decimal = Decimal("0")
    remaining_size: Decimal = Decimal("0")
    current_stop: Optional[Decimal] = None
    current_target: Optional[Decimal] = None
    locked_profit: Decimal = Decimal("0")
    total_booked_pnl: Decimal = Decimal("0")
    snapshot_data: Dict[str, Any] = field(default_factory=dict)
    updated_at: Optional[datetime] = None

@dataclass
class FIFOCloseDetail:
    entry_sequence: int
    entry_price: Decimal
    taken: Decimal
    exit_price: Decimal
    pnl: Decimal
    entry_type: EntryType

@dataclass
class FIFOResult:
    fifo: List[FIFOCloseDetail]
    total_pnl: Decimal
    closed_size: Decimal
    remaining_size: Decimal

    def to_tree_dict(self) -> Dict:
        return {
            "fifo": [
                {
                    "entry_sequence": d.entry_sequence,
                    "entry_price": str(d.entry_price),
                    "taken": str(d.taken),
                    "exit_price": str(d.exit_price),
                    "pnl": str(d.pnl),
                    "entry_type": d.entry_type.value
                }
                for d in self.fifo
            ],
            "total_pnl": str(self.total_pnl),
            "closed_size": str(self.closed_size),
            "remaining_size": str(self.remaining_size)
        }

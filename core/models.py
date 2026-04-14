"""
Core domain models
"""
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any


class TradeStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class EntryType(Enum):
    INITIAL = "initial"
    PYRAMID = "pyramid"


class EventType(Enum):
    TRADE_CREATED = "trade_created"
    STOP_UPDATED = "stop_updated"
    PARTIAL_CLOSE = "partial_close"
    FULL_CLOSE = "full_close"
    PYRAMID_ADDED = "pyramid_added"
    TRADE_CANCELLED = "trade_cancelled"


@dataclass
class TradeEntry:
    entry_price: Decimal
    size: Decimal
    entry_type: EntryType
    sequence: int = 0
    closed_size: Decimal = field(default_factory=lambda: Decimal("0"))
    id: Optional[int] = None

    @property
    def remaining_size(self) -> Decimal:
        return self.size - self.closed_size

    @property
    def is_fully_closed(self) -> bool:
        return self.remaining_size <= 0


@dataclass
class Trade:
    trade_id: str
    symbol: str
    side: str
    asset_class: str
    entries: List[TradeEntry]
    status: TradeStatus
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    @property
    def is_closed(self) -> bool:
        return self.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED)


@dataclass
class FIFOEntryDetail:
    entry_sequence: int
    entry_price: Decimal
    taken: Decimal
    pnl: Decimal


@dataclass
class FIFOResult:
    fifo: List[FIFOEntryDetail]
    total_pnl: Decimal

    def to_tree_dict(self) -> Dict[str, Any]:
        return {
            "entries": [
                {
                    "sequence": d.entry_sequence,
                    "price": str(d.entry_price),
                    "taken": str(d.taken),
                    "pnl": str(d.pnl)
                }
                for d in self.fifo
            ],
            "total_pnl": str(self.total_pnl)
        }


@dataclass
class TradeEvent:
    event_type: EventType
    payload: Dict[str, Any]
    idempotency_key: str
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class TradeSnapshot:
    weighted_avg_entry: Decimal
    total_size: Decimal
    remaining_size: Decimal
    current_stop: Optional[Decimal]
    current_target: Optional[Decimal]
    locked_profit: Decimal
    total_booked_pnl: Decimal = field(default_factory=lambda: Decimal("0"))

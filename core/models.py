"""Core domain models - Config-driven"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    NOT_TRIGGERED = "NOT_TRIGGERED"

class EntryType(Enum):
    INITIAL = "INITIAL"
    PYRAMID = "PYRAMID"

@dataclass
class TradeEntry:
    entry_id: str
    entry_price: float
    size: float
    type: EntryType
    timestamp: float
    closed_size: float = 0.0

    @property
    def remaining_size(self) -> float:
        return self.size - self.closed_size

    @property
    def is_fully_closed(self) -> bool:
        return self.remaining_size <= 0

@dataclass
class FIFOCloseRecord:
    timestamp: float
    close_percentage: float
    exit_price: float
    close_details: List[Dict[str, Any]]
    booked_pnl: float
    remaining_size: float
    new_weighted_avg: float

@dataclass
class TradeUpdate:
    update_type: str
    timestamp: float
    price: Optional[float] = None
    note_text: Optional[str] = None
    percentage: Optional[float] = None
    data: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Trade:
    trade_id: str
    symbol: str
    asset_class: str
    side: str
    entry_price: float
    current_stop: Optional[float] = None
    position_size: float = 1.0
    status: TradeStatus = field(default_factory=lambda: TradeStatus.OPEN)
    locked_profit: float = 0.0
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())
    updates: List[TradeUpdate] = field(default_factory=list)
    leverage_multiplier: int = 1
    entries: List[TradeEntry] = field(default_factory=list)
    fifo_closes: List[FIFOCloseRecord] = field(default_factory=list)
    target: Optional[float] = None
    stop_loss: Optional[float] = None

    @property
    def weighted_avg_entry(self) -> float:
        if not self.entries:
            return self.entry_price
        total_size = sum(e.remaining_size for e in self.entries)
        if total_size == 0:
            return self.entry_price
        weighted_sum = sum(e.entry_price * e.remaining_size for e in self.entries)
        return weighted_sum / total_size

    @property
    def total_position_size(self) -> float:
        return sum(e.remaining_size for e in self.entries)

@dataclass
class OCRResult:
    symbol: str
    asset_class: str
    setup_found: bool
    side: str
    entry: str
    target: str
    stop_loss: str
    is_stock_chart: bool = False
    raw_response: Optional[str] = None
    confidence: float = 0.0

    @property
    def is_valid(self) -> bool:
        return self.setup_found and all([
            self.symbol, self.side, self.entry, self.target, self.stop_loss
        ])

@dataclass
class MessageMapping:
    main_msg_id: int
    tg_channel: int
    tg_msg_ids: List[int] = field(default_factory=list)
    twitter: Dict[str, Any] = field(default_factory=dict)
    parent_main_msg_id: Optional[int] = None
    parent_tg_msg_id: Optional[int] = None
    trade_id: Optional[str] = None
    ocr_symbol: Optional[str] = None
    asset_class: Optional[str] = None
    leverage_multiplier: int = 1
    gemini_result: Optional[Dict] = None
    is_position_update: bool = False
    is_admin_channel: bool = False
    position_update_data: Dict[str, Any] = field(default_factory=dict)
    settings_applied: Dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())

@dataclass
class ParsedCommand:
    command: str
    subcommand: Optional[str] = None
    price: Optional[float] = None
    percentage: Optional[float] = None
    note_text: Optional[str] = None
    size_percentage: Optional[float] = None
    reason: Optional[str] = None
    raw_text: str = ""
    message_type: Optional[str] = None
    update_type: Optional[str] = None

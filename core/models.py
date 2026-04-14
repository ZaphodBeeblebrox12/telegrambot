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

    @property
    def entries_count(self) -> int:
        return len(self.entries)

    def add_entry(self, entry: TradeEntry):
        self.entries.append(entry)

    def add_fifo_close(self, record: FIFOCloseRecord):
        self.fifo_closes.append(record)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'trade_id': self.trade_id,
            'symbol': self.symbol,
            'asset_class': self.asset_class,
            'side': self.side,
            'entry_price': self.entry_price,
            'current_stop': self.current_stop,
            'position_size': self.position_size,
            'status': self.status.value,
            'locked_profit': self.locked_profit,
            'created_at': self.created_at,
            'updates': [
                {
                    'update_type': u.update_type,
                    'timestamp': u.timestamp,
                    'price': u.price,
                    'note_text': u.note_text,
                    'percentage': u.percentage,
                    'data': u.data
                } for u in self.updates
            ],
            'leverage_multiplier': self.leverage_multiplier,
            'entries': [
                {
                    'entry_id': e.entry_id,
                    'entry_price': e.entry_price,
                    'size': e.size,
                    'type': e.type.value,
                    'timestamp': e.timestamp,
                    'closed_size': e.closed_size
                } for e in self.entries
            ],
            'fifo_closes': [
                {
                    'timestamp': f.timestamp,
                    'close_percentage': f.close_percentage,
                    'exit_price': f.exit_price,
                    'close_details': f.close_details,
                    'booked_pnl': f.booked_pnl,
                    'remaining_size': f.remaining_size,
                    'new_weighted_avg': f.new_weighted_avg
                } for f in self.fifo_closes
            ],
            'target': self.target,
            'stop_loss': self.stop_loss
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Trade':
        trade = cls(
            trade_id=data['trade_id'],
            symbol=data['symbol'],
            asset_class=data['asset_class'],
            side=data['side'],
            entry_price=data['entry_price'],
            current_stop=data.get('current_stop'),
            position_size=data.get('position_size', 1.0),
            status=TradeStatus(data.get('status', 'OPEN')),
            locked_profit=data.get('locked_profit', 0.0),
            created_at=data.get('created_at', datetime.now().timestamp()),
            leverage_multiplier=data.get('leverage_multiplier', 1),
            target=data.get('target'),
            stop_loss=data.get('stop_loss')
        )

        for e_data in data.get('entries', []):
            entry = TradeEntry(
                entry_id=e_data['entry_id'],
                entry_price=e_data['entry_price'],
                size=e_data['size'],
                type=EntryType(e_data.get('type', 'INITIAL')),
                timestamp=e_data['timestamp'],
                closed_size=e_data.get('closed_size', 0.0)
            )
            trade.entries.append(entry)

        for u_data in data.get('updates', []):
            update = TradeUpdate(
                update_type=u_data['update_type'],
                timestamp=u_data['timestamp'],
                price=u_data.get('price'),
                note_text=u_data.get('note_text'),
                percentage=u_data.get('percentage'),
                data=u_data.get('data', {})
            )
            trade.updates.append(update)

        for f_data in data.get('fifo_closes', []):
            record = FIFOCloseRecord(
                timestamp=f_data['timestamp'],
                close_percentage=f_data['close_percentage'],
                exit_price=f_data['exit_price'],
                close_details=f_data['close_details'],
                booked_pnl=f_data['booked_pnl'],
                remaining_size=f_data['remaining_size'],
                new_weighted_avg=f_data['new_weighted_avg']
            )
            trade.fifo_closes.append(record)

        return trade


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

    def to_dict(self) -> Dict[str, Any]:
        return {
            'main_msg_id': self.main_msg_id,
            'tg_channel': self.tg_channel,
            'tg_msg_ids': self.tg_msg_ids,
            'twitter': self.twitter,
            'parent_main_msg_id': self.parent_main_msg_id,
            'parent_tg_msg_id': self.parent_tg_msg_id,
            'trade_id': self.trade_id,
            'ocr_symbol': self.ocr_symbol,
            'asset_class': self.asset_class,
            'leverage_multiplier': self.leverage_multiplier,
            'gemini_result': self.gemini_result,
            'is_position_update': self.is_position_update,
            'is_admin_channel': self.is_admin_channel,
            'position_update_data': self.position_update_data,
            'settings_applied': self.settings_applied,
            'created_at': self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MessageMapping':
        return cls(
            main_msg_id=data['main_msg_id'],
            tg_channel=data['tg_channel'],
            tg_msg_ids=data.get('tg_msg_ids', []),
            twitter=data.get('twitter', {}),
            parent_main_msg_id=data.get('parent_main_msg_id'),
            parent_tg_msg_id=data.get('parent_tg_msg_id'),
            trade_id=data.get('trade_id'),
            ocr_symbol=data.get('ocr_symbol'),
            asset_class=data.get('asset_class'),
            leverage_multiplier=data.get('leverage_multiplier', 1),
            gemini_result=data.get('gemini_result'),
            is_position_update=data.get('is_position_update', False),
            is_admin_channel=data.get('is_admin_channel', False),
            position_update_data=data.get('position_update_data', {}),
            settings_applied=data.get('settings_applied', {}),
            created_at=data.get('created_at', datetime.now().timestamp())
        )


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

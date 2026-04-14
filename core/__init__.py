"""
Core Domain - Trade Management Engine
"""

from .models import (
    Trade, TradeEntry, TradeEvent, TradeSnapshot,
    TradeStatus, EntryType, EventType, FIFOCloseDetail, FIFOResult
)
from .fifo import FIFOEngine
from .snapshot import SnapshotBuilder
from .services import TradeService, generate_trade_id, make_idempotency_key
from .repositories import TradeRepository
from .db import Database, TradeModel, TradeEntryModel, TradeEventModel, TradeSnapshotModel, MessageMappingModel

__all__ = [
    'Trade', 'TradeEntry', 'TradeEvent', 'TradeSnapshot',
    'TradeStatus', 'EntryType', 'EventType',
    'FIFOCloseDetail', 'FIFOResult',
    'FIFOEngine', 'SnapshotBuilder', 'TradeService',
    'TradeRepository', 'Database',
    'TradeModel', 'TradeEntryModel', 'TradeEventModel',
    'TradeSnapshotModel', 'MessageMappingModel',
    'generate_trade_id', 'make_idempotency_key'
]

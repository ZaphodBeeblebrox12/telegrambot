"""Core module - Domain models and repositories"""
from core.models import (
    Trade, TradeEntry, TradeUpdate, FIFOCloseRecord,
    OCRResult, MessageMapping, ParsedCommand,
    TradeStatus, EntryType
)
from core.repositories import (
    TradeRepository, MessageMappingRepository,
    SQLTradeRepository, SQLMessageMappingRepository,
    RepositoryFactory
)
from core.fifo import FIFOCloseManager, get_fifo_manager
from core.services import TradeService, get_trade_service
from core.id_generator import TradeIDGenerator, get_id_generator
from core.outbox import (
    OutboxMessage, OutboxStatus, OutboxStore,
    RetryPolicy, AsyncProcessor, OutboxManager,
    get_outbox
)
from core.db import Database, TradeModel, TradeEntryModel, TradeSnapshotModel, MessageMappingModel, OutboxMessageModel

__all__ = [
    'Trade', 'TradeEntry', 'TradeUpdate', 'FIFOCloseRecord',
    'OCRResult', 'MessageMapping', 'ParsedCommand',
    'TradeStatus', 'EntryType',
    'TradeRepository', 'MessageMappingRepository',
    'SQLTradeRepository', 'SQLMessageMappingRepository',
    'RepositoryFactory',
    'FIFOCloseManager', 'get_fifo_manager',
    'TradeService', 'get_trade_service',
    'TradeIDGenerator', 'get_id_generator',
    'OutboxMessage', 'OutboxStatus', 'OutboxStore',
    'RetryPolicy', 'AsyncProcessor', 'OutboxManager', 'get_outbox',
    'Database', 'TradeModel', 'TradeEntryModel', 'TradeSnapshotModel',
    'MessageMappingModel', 'OutboxMessageModel'
]

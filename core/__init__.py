"""Core module - SQL-based FIFO trading engine"""
from core.models import (
    Trade, TradeEntry, TradeStatus,
    EntryType, OCRResult, ParsedCommand, MessageMapping
)
from core.repositories import RepositoryFactory
from core.services import get_trade_service
from core.fifo import get_fifo_manager
from core.id_generator import get_id_generator
from core.db import Database, get_db
from core.outbox import get_outbox, OutboxManager

# Production safety layer exports
from core.rate_limit_manager import get_rate_limit_manager, RateLimitManager
from core.twitter_toggle_manager import get_twitter_toggle_manager, TwitterToggleManager, is_twitter_enabled
from core.twitter_style_manager import get_twitter_style_manager, TwitterStyleManager, should_post_to_twitter

__all__ = [
    # Core models
    'Trade', 'TradeEntry', 'TradeStatus',
    'EntryType', 'OCRResult', 'ParsedCommand', 'MessageMapping',
    # Core services
    'RepositoryFactory', 'get_trade_service', 'get_fifo_manager',
    'get_id_generator', 'get_outbox',
    'Database', 'get_db', 'OutboxManager',
    # Rate limiting
    'get_rate_limit_manager', 'RateLimitManager',
    # Twitter controls
    'get_twitter_toggle_manager', 'TwitterToggleManager', 'is_twitter_enabled',
    'get_twitter_style_manager', 'TwitterStyleManager', 'should_post_to_twitter',
]

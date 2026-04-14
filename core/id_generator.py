"""Deterministic Trade ID Generator - Human Readable"""
import re
from datetime import datetime
from typing import Optional
from core.repositories import RepositoryFactory

class TradeIDGenerator:
    """Generates human-readable trade IDs: SYMBOL-YYYYMMDD-NN"""

    def __init__(self):
        self.repo = RepositoryFactory.get_trade_repository()

    def generate(self, symbol: str, timestamp: Optional[float] = None) -> str:
        """
        Generate trade ID: SYMBOL-YYYYMMDD-NN
        Example: BTCUSD-20260414-01
        """
        # Clean symbol (remove spaces, special chars)
        clean_symbol = re.sub(r'[^A-Z0-9]', '', symbol.upper())

        # Get date
        if timestamp is None:
            timestamp = datetime.now().timestamp()
        dt = datetime.fromtimestamp(timestamp)
        date_str = dt.strftime('%Y%m%d')

        # Find next sequence number for this symbol+date
        existing = self._get_existing_ids(clean_symbol, date_str)
        next_seq = self._get_next_sequence(existing)

        return f"{clean_symbol}-{date_str}-{next_seq:02d}"

    def _get_existing_ids(self, symbol: str, date_str: str) -> list:
        """Get existing trade IDs for symbol on date"""
        prefix = f"{symbol}-{date_str}-"
        all_trades = self.repo.get_all()

        matching = []
        for trade in all_trades:
            if trade.trade_id.startswith(prefix):
                matching.append(trade.trade_id)

        return matching

    def _get_next_sequence(self, existing_ids: list) -> int:
        """Get next sequence number"""
        if not existing_ids:
            return 1

        max_seq = 0
        for tid in existing_ids:
            try:
                seq_part = tid.split('-')[-1]
                seq = int(seq_part)
                max_seq = max(max_seq, seq)
            except (ValueError, IndexError):
                continue

        return max_seq + 1

    def generate_entry_id(self, trade_id: str, entry_type: str, index: int) -> str:
        """Generate entry ID: TRADE-ID-ENTRY-TYPE-NN"""
        prefix = entry_type.upper()[:3]  # INIT or PYR
        return f"{trade_id}-ENTRY-{prefix}-{index:02d}"

_id_generator: Optional[TradeIDGenerator] = None

def get_id_generator() -> TradeIDGenerator:
    global _id_generator
    if _id_generator is None:
        _id_generator = TradeIDGenerator()
    return _id_generator

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
        clean_symbol = re.sub(r'[^A-Z0-9]', '', symbol.upper())
        if timestamp is None:
            timestamp = datetime.now().timestamp()
        dt = datetime.fromtimestamp(timestamp)
        date_str = dt.strftime('%Y%m%d')
        existing = self._get_existing_ids(clean_symbol, date_str)
        next_seq = self._get_next_sequence(existing)
        return f"{clean_symbol}-{date_str}-{next_seq:02d}"

    def _get_existing_ids(self, symbol: str, date_str: str) -> list:
        prefix = f"{symbol}-{date_str}-"
        all_trades = self.repo.get_all()
        return [t.trade_id for t in all_trades if t.trade_id.startswith(prefix)]

    def _get_next_sequence(self, existing_ids: list) -> int:
        if not existing_ids:
            return 1
        max_seq = 0
        for tid in existing_ids:
            try:
                seq = int(tid.split('-')[-1])
                max_seq = max(max_seq, seq)
            except (ValueError, IndexError):
                continue
        return max_seq + 1

    def generate_entry_id(self, trade_id: str, entry_type: str, index: int) -> str:
        prefix = entry_type.upper()[:3]
        return f"{trade_id}-ENTRY-{prefix}-{index:02d}"

_id_generator = None

def get_id_generator():
    global _id_generator
    if _id_generator is None:
        _id_generator = TradeIDGenerator()
    return _id_generator

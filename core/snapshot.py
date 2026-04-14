"""Snapshot Builder - Helper for weighted average calculations"""
from decimal import Decimal
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import TradeEntry

class SnapshotBuilder:
    """Builds trade snapshots - helper class only."""

    @staticmethod
    def calculate_weighted_avg(entries: List["TradeEntry"]) -> Decimal:
        """Calculate weighted average entry price."""
        total_value = sum(e.entry_price * e.remaining_size for e in entries)
        total_size = sum(e.remaining_size for e in entries)
        if total_size <= 0:
            return Decimal("0")
        return Decimal(str(total_value)) / Decimal(str(total_size))

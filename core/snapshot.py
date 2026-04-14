"""
Snapshot Builder - Weighted average calculations
"""
from decimal import Decimal
from typing import List

from .models import TradeEntry


class SnapshotBuilder:
    """Builds trade snapshots - weighted average only."""

    def calculate_weighted_avg(self, entries: List[TradeEntry]) -> Decimal:
        """Calculate weighted average entry price."""
        total_value = sum(e.entry_price * e.remaining_size for e in entries)
        total_size = sum(e.remaining_size for e in entries)

        if total_size <= 0:
            return Decimal("0")

        return total_value / total_size

    def calculate_locked_profit(
        self,
        side: str,
        weighted_avg: Decimal,
        stop_price: Decimal,
        position_size: Decimal
    ) -> Decimal:
        """Calculate locked profit based on stop position."""
        if side.upper() == "LONG":
            return (stop_price - weighted_avg) * position_size
        else:
            return (weighted_avg - stop_price) * position_size

"""
Snapshot Builder - Weighted average calculations (FIX 5: Helper only)
"""
from decimal import Decimal
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import TradeEntry

class SnapshotBuilder:
    """Builds trade snapshots - helper class only (FIX 5).

    All primary calculations now centralized in TradeService.
    This class provides optional utility methods only.
    """

    @staticmethod
    def calculate_weighted_avg(entries: List["TradeEntry"]) -> Decimal:
        """Calculate weighted average entry price (helper method)."""
        total_value = sum(e.entry_price * e.remaining_size for e in entries)
        total_size = sum(e.remaining_size for e in entries)

        if total_size <= 0:
            return Decimal("0")

        return Decimal(str(total_value)) / Decimal(str(total_size))

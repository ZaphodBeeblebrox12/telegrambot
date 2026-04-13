"""
Snapshot Builder - Derived state calculations
"""

from decimal import Decimal
from typing import Optional

from .models import TradeSnapshot


class SnapshotBuilder:
    def __init__(self):
        self.precision = Decimal("0.00000001")

    def calculate_locked_profit(
        self, 
        side: str, 
        weighted_avg: Decimal, 
        current_stop: Decimal,
        remaining_size: Decimal
    ) -> Decimal:
        if side == "LONG":
            if current_stop > weighted_avg:
                return ((current_stop - weighted_avg) * remaining_size).quantize(self.precision)
        else:
            if current_stop < weighted_avg:
                return ((weighted_avg - current_stop) * remaining_size).quantize(self.precision)

        return Decimal("0")

    def calculate_weighted_avg(self, entries) -> Decimal:
        total_value = Decimal("0")
        total_size = Decimal("0")

        for entry in entries:
            remaining = entry.remaining_size
            if remaining > 0:
                total_value += entry.entry_price * remaining
                total_size += remaining

        if total_size == 0:
            return Decimal("0")

        return (total_value / total_size).quantize(self.precision)

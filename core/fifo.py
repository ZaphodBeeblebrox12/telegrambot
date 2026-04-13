"""
FIFO Engine - Position closing calculations
"""

from decimal import Decimal
from typing import List, Tuple

from .models import Trade, TradeEntry, FIFOCloseDetail, FIFOResult, EntryType


class FIFOEngine:
    def __init__(self, precision: int = 8):
        self.precision = Decimal(f"1.{'0' * precision}")

    def calculate_close(
        self, 
        trade: Trade, 
        close_percentage: Decimal, 
        exit_price: Decimal
    ) -> FIFOResult:
        if trade.is_closed:
            raise ValueError(f"Cannot close trade with status {trade.status}")

        if close_percentage <= 0 or close_percentage > 100:
            raise ValueError("Close percentage must be between 0 and 100")

        total_size = trade.total_size
        if total_size == 0:
            raise ValueError("Trade has no position size")

        close_size = (total_size * close_percentage / 100).quantize(self.precision)
        remaining_to_close = close_size
        fifo_details: List[FIFOCloseDetail] = []
        total_pnl = Decimal("0")

        for entry in sorted(trade.entries, key=lambda e: e.sequence):
            if remaining_to_close <= 0:
                break

            if entry.is_fully_closed:
                continue

            available = entry.remaining_size
            take = min(available, remaining_to_close)

            if trade.side == "LONG":
                pnl = (exit_price - entry.entry_price) * take
            else:
                pnl = (entry.entry_price - exit_price) * take

            fifo_details.append(FIFOCloseDetail(
                entry_sequence=entry.sequence,
                entry_price=entry.entry_price,
                taken=take,
                exit_price=exit_price,
                pnl=pnl,
                entry_type=entry.entry_type
            ))

            total_pnl += pnl
            remaining_to_close -= take

        return FIFOResult(
            fifo=fifo_details,
            total_pnl=total_pnl.quantize(self.precision),
            closed_size=close_size,
            remaining_size=trade.total_size - close_size
        )

    def apply_close(
        self, 
        trade: Trade, 
        close_percentage: Decimal, 
        exit_price: Decimal
    ) -> FIFOResult:
        result = self.calculate_close(trade, close_percentage, exit_price)

        # O(n) - dict lookup
        close_by_sequence = {d.entry_sequence: d.taken for d in result.fifo}
        for entry in trade.entries:
            if entry.sequence in close_by_sequence:
                entry.closed_size += close_by_sequence[entry.sequence]

        return result

    def calculate_weighted_avg(self, entries: List[TradeEntry]) -> Decimal:
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

    def validate_pyramid_entry(
        self, 
        trade: Trade, 
        entry_price: Decimal,
        min_price_move_pct: Decimal = Decimal("0.5")
    ) -> Tuple[bool, str]:
        if not trade.entries:
            return False, "Trade has no entries"

        original_entry = trade.entries[0].entry_price

        if trade.side == "LONG":
            if entry_price <= original_entry:
                return False, f"Pyramid price must be > original entry for LONG"

            price_move_pct = ((entry_price - original_entry) / original_entry) * 100
            if price_move_pct < min_price_move_pct:
                return False, f"Price move {price_move_pct:.2f}% < minimum {min_price_move_pct}%"
        else:
            if entry_price >= original_entry:
                return False, f"Pyramid price must be < original entry for SHORT"

            price_move_pct = ((original_entry - entry_price) / original_entry) * 100
            if price_move_pct < min_price_move_pct:
                return False, f"Price move {price_move_pct:.2f}% < minimum {min_price_move_pct}%"

        return True, "Valid"

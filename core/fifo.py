"""
FIFO Engine - O(n) partial close calculations
"""
from decimal import Decimal
from typing import List, Tuple

from .models import Trade, TradeEntry, FIFOResult, FIFOEntryDetail


class FIFOEngine:
    """O(n) FIFO calculation engine."""

    def calculate_close(
        self,
        trade: Trade,
        close_percentage: Decimal,
        exit_price: Decimal
    ) -> FIFOResult:
        """Calculate FIFO close with O(n) complexity."""
        total_size = sum(e.remaining_size for e in trade.entries)
        close_size = total_size * (close_percentage / 100)

        details = []
        remaining_to_close = close_size
        total_pnl = Decimal("0")

        for entry in sorted(trade.entries, key=lambda x: x.sequence):
            if remaining_to_close <= 0:
                break

            available = entry.remaining_size
            if available <= 0:
                continue

            take = min(available, remaining_to_close)

            # Calculate PnL
            if trade.side.upper() == "LONG":
                pnl = (exit_price - entry.entry_price) * take
            else:
                pnl = (entry.entry_price - exit_price) * take

            details.append(FIFOEntryDetail(
                entry_sequence=entry.sequence,
                entry_price=entry.entry_price,
                taken=take,
                pnl=pnl
            ))

            total_pnl += pnl
            remaining_to_close -= take

        return FIFOResult(fifo=details, total_pnl=total_pnl)

    def validate_pyramid_entry(
        self,
        trade: Trade,
        entry_price: Decimal
    ) -> Tuple[bool, str]:
        """Validate pyramid entry can be added."""
        if not trade.entries:
            return False, "No existing entries"

        # Get weighted average of existing entries
        total_size = sum(e.remaining_size for e in trade.entries)
        if total_size <= 0:
            return False, "No remaining position"

        weighted_avg = sum(
            e.entry_price * e.remaining_size for e in trade.entries
        ) / total_size

        # For longs, pyramid entry must be above weighted avg
        # For shorts, pyramid entry must be below weighted avg
        if trade.side.upper() == "LONG":
            if entry_price <= weighted_avg:
                return False, f"Pyramid entry {entry_price} must be above weighted avg {weighted_avg:.5f}"
        else:
            if entry_price >= weighted_avg:
                return False, f"Pyramid entry {entry_price} must be below weighted avg {weighted_avg:.5f}"

        return True, "Valid"

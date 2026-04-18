"""
Production-Grade FIFO Engine
- Transaction safety
- Row locking (SELECT FOR UPDATE)
- MySQL/PostgreSQL compatible
- No state mutation without DB commit
"""
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import logging

from sqlalchemy.orm import Session
from sqlalchemy import select, update

logger = logging.getLogger(__name__)


@dataclass
class FIFOCloseResult:
    """Result of FIFO close operation"""
    entry_id: str
    entry_price: Decimal
    closed_size: Decimal
    pnl: Decimal
    remaining_in_entry: Decimal


@dataclass
class FIFOCalculation:
    """Complete FIFO calculation result"""
    close_details: List[FIFOCloseResult]
    total_pnl: Decimal
    remaining_size: Decimal
    new_weighted_avg: Decimal
    tree_lines: List[str]


class FIFOEngine:
    """
    Production-grade FIFO engine.

    RULES:
    1. All calculations use Decimal for precision
    2. Never mutate state outside transaction
    3. Always use SELECT FOR UPDATE on entries
    4. closed_size is cumulative (never decreases)
    """

    def __init__(self, session: Session):
        self.session = session
        self.precision = Decimal('0.00000001')  # 8 decimal places

    def _to_decimal(self, value) -> Decimal:
        """Convert value to Decimal with proper precision"""
        if isinstance(value, Decimal):
            return value.quantize(self.precision, rounding=ROUND_HALF_UP)
        return Decimal(str(value)).quantize(self.precision, rounding=ROUND_HALF_UP)

    def calculate_fifo_close(
        self,
        trade_id: int,
        exit_price: Decimal,
        close_percentage: Decimal,
        side: str
    ) -> FIFOCalculation:
        """
        Calculate FIFO close with row locking.

        Args:
            trade_id: Database ID (not trade_id string)
            exit_price: Exit price
            close_percentage: Percentage of REMAINING position to close
            side: 'LONG' or 'SHORT'

        Returns:
            FIFOCalculation with all close details
        """
        from core.db import TradeEntryModel

        # LOCK entries for this trade (prevents concurrent modification)
        entries = self.session.execute(
            select(TradeEntryModel)
            .where(TradeEntryModel.trade_id == trade_id)
            .order_by(TradeEntryModel.sequence.asc())
            .with_for_update()  # SELECT FOR UPDATE
        ).scalars().all()

        if not entries:
            return FIFOCalculation(
                close_details=[],
                total_pnl=Decimal('0'),
                remaining_size=Decimal('0'),
                new_weighted_avg=Decimal('0'),
                tree_lines=[]
            )

        # Calculate total remaining (size - closed_size)
        total_remaining = sum(
            self._to_decimal(e.size) - self._to_decimal(e.closed_size)
            for e in entries
        )

        if total_remaining <= 0:
            return FIFOCalculation(
                close_details=[],
                total_pnl=Decimal('0'),
                remaining_size=Decimal('0'),
                new_weighted_avg=Decimal('0'),
                tree_lines=[]
            )

        # Amount to close
        close_amount = (total_remaining * close_percentage) / Decimal('100')
        remaining_to_close = close_amount

        close_details: List[FIFOCloseResult] = []
        total_pnl = Decimal('0')

        # Process FIFO
        for entry in entries:
            if remaining_to_close <= 0:
                break

            entry_size = self._to_decimal(entry.size)
            entry_closed = self._to_decimal(entry.closed_size)
            entry_remaining = entry_size - entry_closed

            if entry_remaining <= 0:
                continue

            close_from_entry = min(entry_remaining, remaining_to_close)
            entry_price = self._to_decimal(entry.entry_price)

            # Calculate PnL
            if side == 'LONG':
                pnl = (exit_price - entry_price) * close_from_entry
            else:
                pnl = (entry_price - exit_price) * close_from_entry

            close_details.append(FIFOCloseResult(
                entry_id=f"{entry.trade_id}-E{entry.sequence}",
                entry_price=entry_price,
                closed_size=close_from_entry,
                pnl=pnl,
                remaining_in_entry=entry_remaining - close_from_entry
            ))

            total_pnl += pnl
            remaining_to_close -= close_from_entry

        # Calculate new weighted average
        new_remaining_size = total_remaining - close_amount
        new_weighted_avg = Decimal('0')

        if new_remaining_size > 0:
            weighted_sum = Decimal('0')
            for entry in entries:
                entry_size = self._to_decimal(entry.size)
                entry_closed = self._to_decimal(entry.closed_size)
                entry_remaining = entry_size - entry_closed

                # Subtract what we're closing from this entry
                for detail in close_details:
                    if detail.entry_id == f"{entry.trade_id}-E{entry.sequence}":
                        entry_remaining -= detail.closed_size
                        break

                if entry_remaining > 0:
                    weighted_sum += self._to_decimal(entry.entry_price) * entry_remaining

            new_weighted_avg = weighted_sum / new_remaining_size

        # Generate tree lines for display
        tree_lines = self._format_tree_lines(entries, close_details, close_percentage)

        return FIFOCalculation(
            close_details=close_details,
            total_pnl=total_pnl.quantize(self.precision),
            remaining_size=new_remaining_size.quantize(self.precision),
            new_weighted_avg=new_weighted_avg.quantize(self.precision),
            tree_lines=tree_lines
        )

    def apply_close_to_entries(
        self,
        trade_id: int,
        close_details: List[FIFOCloseResult]
    ) -> None:
        """
        Apply close by updating closed_size in DB.
        Must be called within transaction.
        """
        from core.db import TradeEntryModel

        for detail in close_details:
            # Extract sequence from entry_id (format: {trade_id}-E{sequence})
            seq = int(detail.entry_id.split('-E')[-1])

            # Use atomic update
            self.session.execute(
                update(TradeEntryModel)
                .where(
                    TradeEntryModel.trade_id == trade_id,
                    TradeEntryModel.sequence == seq
                )
                .values(
                    closed_size=TradeEntryModel.closed_size + float(detail.closed_size)
                )
            )

    def _format_tree_lines(
        self,
        entries: List[Any],
        close_details: List[FIFOCloseResult],
        close_percentage: Decimal
    ) -> List[str]:
        """Format FIFO tree visualization lines"""
        lines = []
        closed_map = {d.entry_id: d for d in close_details}

        for i, entry in enumerate(entries):
            is_last = (i == len(entries) - 1)
            prefix = "└─" if is_last else "├─"

            entry_id = f"{entry.trade_id}-E{entry.sequence}"
            entry_price = self._to_decimal(entry.entry_price)

            if entry_id in closed_map:
                detail = closed_map[entry_id]
                total_size = self._to_decimal(entry.size)
                closed_pct = (detail.closed_size / total_size) * Decimal('100')
                line = f"{prefix} {entry_price} [{int(closed_pct)}% closed]"
            else:
                remaining = self._to_decimal(entry.size) - self._to_decimal(entry.closed_size)
                if remaining > 0:
                    line = f"{prefix} {entry_price}"
                else:
                    line = f"{prefix} {entry_price} [fully closed]"

            # Enforce 35 char limit
            if len(line) > 35:
                line = line[:34] + "…"

            lines.append(line)

        return lines

    def get_remaining_position(self, trade_id: int) -> Tuple[Decimal, Decimal]:
        """
        Get total remaining size and weighted avg for a trade.
        Uses row locking.
        """
        from core.db import TradeEntryModel

        entries = self.session.execute(
            select(TradeEntryModel)
            .where(TradeEntryModel.trade_id == trade_id)
            .with_for_update()
        ).scalars().all()

        total_remaining = Decimal('0')
        weighted_sum = Decimal('0')

        for entry in entries:
            remaining = self._to_decimal(entry.size) - self._to_decimal(entry.closed_size)
            if remaining > 0:
                total_remaining += remaining
                weighted_sum += self._to_decimal(entry.entry_price) * remaining

        weighted_avg = weighted_sum / total_remaining if total_remaining > 0 else Decimal('0')

        return total_remaining.quantize(self.precision), weighted_avg.quantize(self.precision)

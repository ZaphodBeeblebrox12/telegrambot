"""FIFO Engine - Config-driven First-In-First-Out position management"""
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime

from config.config_loader import config
from core.models import TradeEntry, FIFOCloseRecord

@dataclass
class FIFOCloseDetail:
    entry_id: str
    entry_price: float
    closed_size: float
    pnl: float

class FIFOCloseManager:
    """Manages FIFO closes for trade positions - Config-driven"""

    def __init__(self):
        self.cfg = config.fifo_settings
        self.tree_prefixes = self.cfg.get("tree_prefixes", {"branch": "├─", "end": "└─"})

    def _get_remaining(self, entry: TradeEntry) -> float:
        """Calculate remaining from closed_size - SINGLE SOURCE OF TRUTH"""
        return entry.size - entry.closed_size

    def calculate_fifo_close(
        self,
        entries: List[TradeEntry],
        exit_price: float,
        close_percentage: float,
        side: str
    ) -> Tuple[List[FIFOCloseDetail], float, float, float]:
        """
        Calculate FIFO close using ONLY closed_size as source of truth.
        remaining = size - closed_size (calculated fresh each time)
        """
        if not entries:
            return [], 0.0, 0.0, 0.0

        # Calculate total remaining from closed_size ONLY
        entries_with_remaining = [(e, self._get_remaining(e)) for e in entries]
        total_remaining = sum(rem for _, rem in entries_with_remaining)

        if total_remaining <= 0:
            return [], 0.0, 0.0, 0.0

        close_amount = total_remaining * (close_percentage / 100)
        remaining_to_close = close_amount

        close_details: List[FIFOCloseDetail] = []
        total_pnl = 0.0

        # Process entries in FIFO order
        for entry, actual_remaining in entries_with_remaining:
            if remaining_to_close <= 0:
                break
            if actual_remaining <= 0:
                continue

            close_from_entry = min(actual_remaining, remaining_to_close)

            if side == "LONG":
                pnl = (exit_price - entry.entry_price) * close_from_entry
            else:
                pnl = (entry.entry_price - exit_price) * close_from_entry

            close_details.append(FIFOCloseDetail(
                entry_id=entry.entry_id,
                entry_price=entry.entry_price,
                closed_size=close_from_entry,
                pnl=pnl
            ))

            total_pnl += pnl
            remaining_to_close -= close_from_entry

        new_remaining_size = total_remaining - close_amount

        # Calculate new weighted average based on what REMAINS after this close
        if new_remaining_size > 0:
            weighted_sum = 0.0
            for entry, actual_remaining in entries_with_remaining:
                # Subtract what we're closing from this entry
                remaining_after = actual_remaining
                for detail in close_details:
                    if detail.entry_id == entry.entry_id:
                        remaining_after -= detail.closed_size
                        break
                if remaining_after > 0:
                    weighted_sum += entry.entry_price * remaining_after
            new_weighted_avg = weighted_sum / new_remaining_size
        else:
            new_weighted_avg = 0.0

        return close_details, total_pnl, new_remaining_size, new_weighted_avg

    def apply_close(self, entries: List[TradeEntry], close_details: List[FIFOCloseDetail]) -> None:
        """Apply close by ACCUMULATING closed_size (never subtracting)"""
        for detail in close_details:
            for entry in entries:
                if entry.entry_id == detail.entry_id:
                    entry.closed_size += detail.closed_size
                    break

    def format_fifo_tree(
        self,
        entries: List[TradeEntry],
        close_details: List[FIFOCloseDetail],
        symbol: str,
        header: str,
        booked_pnl: float,
        remaining_size: float,
        weighted_avg: float,
        current_stop: float,
        leverage: int,
        platform: str = "telegram"
    ) -> str:
        cfg = config.fifo_settings
        max_line_length = cfg.get("max_line_length", 35)

        lines = [header]
        closed_entry_ids = {d.entry_id for d in close_details}

        for i, entry in enumerate(entries):
            is_last = (i == len(entries) - 1)
            prefix = self.tree_prefixes.get("end", "└─") if is_last else self.tree_prefixes.get("branch", "├─")

            # Calculate remaining from closed_size for display
            remaining = self._get_remaining(entry)

            if entry.entry_id in closed_entry_ids:
                detail = next(d for d in close_details if d.entry_id == entry.entry_id)
                closed_pct = (detail.closed_size / entry.size) * 100
                indicator = cfg.get("closed_indicator_format", "[{percentage}%]").format(percentage=int(closed_pct))
                line = f"{prefix} {entry.entry_price} {indicator}"
            else:
                line = f"{prefix} {entry.entry_price}"

            if len(line) > max_line_length:
                line = line[:max_line_length-1] + "…"

            lines.append(line)

        if platform == "telegram":
            lines.append("")
            lines.append(f"• Booked: {booked_pnl:+.2f}")
            if cfg.get("show_remaining_position", True):
                lines.append(f"• Remaining: {remaining_size:.2f}x at {weighted_avg:.2f}")
            if cfg.get("show_weighted_average", True):
                lines.append(f"• Stop: {current_stop:.2f}")
            lines.append(f"• Leverage: {leverage}:1")
        else:
            lines.append(f"Booked: {booked_pnl:+.2f}")

        return "\n".join(lines)

    def create_close_record(
        self,
        close_percentage: float,
        exit_price: float,
        close_details: List[FIFOCloseDetail],
        booked_pnl: float,
        remaining_size: float,
        new_weighted_avg: float
    ) -> FIFOCloseRecord:
        return FIFOCloseRecord(
            timestamp=datetime.now().timestamp(),
            close_percentage=close_percentage,
            exit_price=exit_price,
            close_details=[
                {
                    "entry_id": d.entry_id,
                    "entry_price": d.entry_price,
                    "closed_size": d.closed_size,
                    "pnl": d.pnl
                } for d in close_details
            ],
            booked_pnl=booked_pnl,
            remaining_size=remaining_size,
            new_weighted_avg=new_weighted_avg
        )

_fifo_manager = None

def get_fifo_manager() -> FIFOCloseManager:
    global _fifo_manager
    if _fifo_manager is None:
        _fifo_manager = FIFOCloseManager()
    return _fifo_manager

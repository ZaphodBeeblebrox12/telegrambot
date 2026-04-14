"""
TradeService - Business Logic (CRITICAL FIXES APPLIED)

Fixes:
- NO UUID (base36 timestamp trade IDs)
- Deterministic idempotency keys
- Snapshot loaded from DB first
- FIFO O(n) performance
- SnapshotBuilder ONLY for weighted average
"""

import logging
import time
from contextlib import contextmanager
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any

from .models import Trade, TradeEntry, TradeStatus, EntryType, EventType, FIFOResult, TradeEvent
from .repositories import TradeRepository
from .fifo import FIFOEngine
from .snapshot import SnapshotBuilder
from .db import Database

logger = logging.getLogger(__name__)

def generate_trade_id() -> str:
    """Generate collision-resistant trade ID using base36 timestamp"""
    import random
    import string

    timestamp = int(time.time() * 1000)
    base36 = _to_base36(timestamp)
    time_part = base36[-6:] if len(base36) >= 6 else base36
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=2))

    return f"T{time_part}{random_part}"

def _to_base36(n: int) -> str:
    """Convert integer to base36 string"""
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if n == 0:
        return "0"
    result = ""
    while n > 0:
        n, remainder = divmod(n, 36)
        result = alphabet[remainder] + result
    return result

def make_idempotency_key(operation: str, trade_id: str, **params) -> str:
    """Generate deterministic idempotency key"""
    parts = [operation.upper(), trade_id]

    if "percentage" in params:
        parts.append(str(params["percentage"]))
    if "price" in params:
        parts.append(str(params["price"]))
    if "size" in params:
        parts.append(str(params["size"]))

    return ":".join(parts)

class TradeService:
    def __init__(self, db: Database):
        self.db = db
        self.repo = TradeRepository(db)
        self.fifo = FIFOEngine()
        self.snapshot_builder = SnapshotBuilder()

    @contextmanager
    def _transaction(self):
        with self.repo.session() as session:
            yield session

    def create_trade(
        self,
        symbol: str,
        side: str,
        asset_class: str,
        entry_price: Decimal,
        stop_loss: Optional[Decimal] = None,
        target: Optional[Decimal] = None,
        custom_trade_id: Optional[str] = None
    ) -> Trade:
        with self._transaction() as session:
            trade_id = custom_trade_id or generate_trade_id()

            while self.repo.get_by_trade_id(trade_id, session):
                trade_id = generate_trade_id()

            initial_entry = TradeEntry(
                entry_price=entry_price,
                size=Decimal("1.0"),
                entry_type=EntryType.INITIAL
            )

            trade = Trade(
                trade_id=trade_id,
                symbol=symbol,
                side=side,
                asset_class=asset_class,
                entries=[initial_entry],
                status=TradeStatus.OPEN
            )

            trade = self.repo.create_trade(trade, initial_entry, session)

            idem_key = make_idempotency_key("CREATE", trade_id)

            event = TradeEvent(
                event_type=EventType.TRADE_CREATED,
                payload={
                    "symbol": symbol,
                    "side": side,
                    "asset_class": asset_class,
                    "entry_price": str(entry_price),
                    "stop_loss": str(stop_loss) if stop_loss else None,
                    "target": str(target) if target else None
                },
                idempotency_key=idem_key
            )
            self.repo.insert_event(trade.id, event, session)

            # Build initial snapshot using SnapshotBuilder ONLY
            total_size = sum(e.size for e in trade.entries)
            remaining_size = sum(e.remaining_size for e in trade.entries)
            weighted_avg = self.snapshot_builder.calculate_weighted_avg(trade.entries)

            locked_profit = Decimal("0")
            if stop_loss:
                locked_profit = self.snapshot_builder.calculate_locked_profit(
                    side, weighted_avg, stop_loss, remaining_size
                )

            from .models import TradeSnapshot
            snapshot = TradeSnapshot(
                weighted_avg_entry=weighted_avg,
                total_size=total_size,
                remaining_size=remaining_size,
                current_stop=stop_loss,
                current_target=target,
                locked_profit=locked_profit
            )
            self.repo.save_snapshot(trade.id, snapshot, session)

            logger.info(f"TRADE_CREATED: {trade.trade_id}")
            return trade

    def update_stop(
        self,
        trade_id: str,
        new_stop: Decimal
    ) -> Tuple[bool, str]:
        with self._transaction() as session:
            trade = self.repo.get_by_trade_id(trade_id, session)
            if not trade:
                return False, "Trade not found"

            if trade.is_closed:
                return False, f"Cannot update stop on {trade.status.value} trade"

            idem_key = make_idempotency_key("STOP", trade_id, price=new_stop)
            if self.repo.check_idempotency(idem_key, session):
                return True, "Already processed"

            # Load snapshot from DB
            snapshot = self.repo.get_snapshot(trade.id, session)
            if not snapshot:
                return False, "No snapshot found"

            old_stop = snapshot.current_stop

            event = TradeEvent(
                event_type=EventType.STOP_UPDATED,
                payload={
                    "old_stop": str(old_stop) if old_stop else None,
                    "new_stop": str(new_stop),
                    "trade_id": trade_id
                },
                idempotency_key=idem_key
            )
            self.repo.insert_event(trade.id, event, session)

            # Update snapshot values
            snapshot.current_stop = new_stop
            snapshot.locked_profit = self.snapshot_builder.calculate_locked_profit(
                trade.side, snapshot.weighted_avg_entry, new_stop, snapshot.remaining_size
            )
            self.repo.save_snapshot(trade.id, snapshot, session)

            logger.info(f"STOP_UPDATED: {trade_id}")
            return True, f"Stop updated to {new_stop}"

    def partial_close(
        self,
        trade_id: str,
        close_percentage: Decimal,
        exit_price: Decimal
    ) -> Tuple[bool, Optional[FIFOResult], str]:
        with self._transaction() as session:
            trade = self.repo.get_by_trade_id(trade_id, session)
            if not trade:
                return False, None, "Trade not found"

            if trade.is_closed:
                return False, None, f"Cannot close {trade.status.value} trade"

            idem_key = make_idempotency_key("PARTIAL", trade_id, percentage=close_percentage, price=exit_price)
            if self.repo.check_idempotency(idem_key, session):
                return True, None, "Already processed"

            try:
                result = self.fifo.calculate_close(trade, close_percentage, exit_price)
            except ValueError as e:
                return False, None, str(e)

            # O(n) - apply closes
            close_by_sequence = {d.entry_sequence: d.taken for d in result.fifo}
            for entry in trade.entries:
                if entry.sequence in close_by_sequence:
                    entry.closed_size += close_by_sequence[entry.sequence]
                    if entry.id:
                        self.repo.update_entry_closed_size(entry.id, entry.closed_size, session)

            event = TradeEvent(
                event_type=EventType.PARTIAL_CLOSE,
                payload={
                    "close_percentage": str(close_percentage),
                    "exit_price": str(exit_price),
                    "fifo_result": result.to_tree_dict(),
                    "total_pnl": str(result.total_pnl)
                },
                idempotency_key=idem_key
            )
            self.repo.insert_event(trade.id, event, session)

            # Load snapshot from DB and update
            snapshot = self.repo.get_snapshot(trade.id, session)
            if not snapshot:
                return False, None, "No snapshot found"

            snapshot.total_booked_pnl += result.total_pnl
            snapshot.remaining_size = sum(e.remaining_size for e in trade.entries)

            # Recalculate weighted avg using SnapshotBuilder ONLY
            remaining_entries = [e for e in trade.entries if e.remaining_size > 0]
            if remaining_entries:
                snapshot.weighted_avg_entry = self.snapshot_builder.calculate_weighted_avg(remaining_entries)

            if snapshot.current_stop:
                snapshot.locked_profit = self.snapshot_builder.calculate_locked_profit(
                    trade.side, snapshot.weighted_avg_entry,
                    snapshot.current_stop, snapshot.remaining_size
                )

            self.repo.save_snapshot(trade.id, snapshot, session)

            logger.info(f"PARTIAL_CLOSE: {trade_id} | {close_percentage}%")
            return True, result, f"Closed {close_percentage}%"

    def full_close(
        self,
        trade_id: str,
        exit_price: Decimal,
        close_reason: str = "manual"
    ) -> Tuple[bool, Optional[FIFOResult], str]:
        with self._transaction() as session:
            trade = self.repo.get_by_trade_id(trade_id, session)
            if not trade:
                return False, None, "Trade not found"

            if trade.is_closed:
                return False, None, f"Trade already {trade.status.value}"

            idem_key = make_idempotency_key("CLOSE", trade_id, price=exit_price)
            if self.repo.check_idempotency(idem_key, session):
                return True, None, "Already processed"

            result = self.fifo.calculate_close(trade, Decimal("100"), exit_price)

            # O(n) - apply closes
            close_by_sequence = {d.entry_sequence: d.taken for d in result.fifo}
            for entry in trade.entries:
                if entry.sequence in close_by_sequence:
                    entry.closed_size += close_by_sequence[entry.sequence]
                    if entry.id:
                        self.repo.update_entry_closed_size(entry.id, entry.closed_size, session)

            self.repo.update_trade_status(trade.id, TradeStatus.CLOSED, session)

            event = TradeEvent(
                event_type=EventType.FULL_CLOSE,
                payload={
                    "exit_price": str(exit_price),
                    "final_pnl": str(result.total_pnl),
                    "reason": close_reason
                },
                idempotency_key=idem_key
            )
            self.repo.insert_event(trade.id, event, session)

            # Update snapshot
            snapshot = self.repo.get_snapshot(trade.id, session)
            if snapshot:
                snapshot.total_booked_pnl += result.total_pnl
                snapshot.remaining_size = Decimal("0")
                snapshot.locked_profit = Decimal("0")
                self.repo.save_snapshot(trade.id, snapshot, session)

            logger.info(f"FULL_CLOSE: {trade_id}")
            return True, result, f"Trade closed"

    def pyramid_add(
        self,
        trade_id: str,
        entry_price: Decimal,
        size_percentage: Decimal = Decimal("100")
    ) -> Tuple[bool, str]:
        with self._transaction() as session:
            trade = self.repo.get_by_trade_id(trade_id, session)
            if not trade:
                return False, "Trade not found"

            if trade.is_closed:
                return False, f"Cannot pyramid on {trade.status.value} trade"

            idem_key = make_idempotency_key("PYRAMID", trade_id, price=entry_price, size=size_percentage)
            if self.repo.check_idempotency(idem_key, session):
                return True, "Already processed"

            valid, msg = self.fifo.validate_pyramid_entry(trade, entry_price)
            if not valid:
                return False, msg

            size = size_percentage / 100
            new_entry = TradeEntry(
                entry_price=entry_price,
                size=size,
                entry_type=EntryType.PYRAMID
            )

            self.repo.add_entry(trade.id, new_entry, session)
            trade.entries.append(new_entry)

            event = TradeEvent(
                event_type=EventType.PYRAMID_ADDED,
                payload={
                    "entry_price": str(entry_price),
                    "size": str(size),
                    "size_percentage": str(size_percentage)
                },
                idempotency_key=idem_key
            )
            self.repo.insert_event(trade.id, event, session)

            # Load snapshot and update using SnapshotBuilder ONLY
            snapshot = self.repo.get_snapshot(trade.id, session)
            if not snapshot:
                return False, "No snapshot found"

            snapshot.weighted_avg_entry = self.snapshot_builder.calculate_weighted_avg(trade.entries)
            snapshot.total_size = sum(e.size for e in trade.entries)
            snapshot.remaining_size = sum(e.remaining_size for e in trade.entries)

            if snapshot.current_stop:
                snapshot.locked_profit = self.snapshot_builder.calculate_locked_profit(
                    trade.side, snapshot.weighted_avg_entry,
                    snapshot.current_stop, snapshot.remaining_size
                )

            self.repo.save_snapshot(trade.id, snapshot, session)

            logger.info(f"PYRAMID_ADD: {trade_id}")
            return True, f"Pyramid added"

    def get_trade_status(self, trade_id: str) -> Optional[Dict[str, Any]]:
        with self._transaction() as session:
            trade = self.repo.get_by_trade_id(trade_id, session)
            if not trade:
                return None

            snapshot = self.repo.get_snapshot(trade.id, session)

            return {
                "id": trade.id,
                "trade_id": trade.trade_id,
                "symbol": trade.symbol,
                "side": trade.side,
                "status": trade.status.value,
                "entries": [
                    {
                        "sequence": e.sequence,
                        "type": e.entry_type.value,
                        "price": str(e.entry_price),
                        "size": str(e.size),
                        "closed": str(e.closed_size),
                        "remaining": str(e.remaining_size),
                        "is_closed": e.is_fully_closed
                    }
                    for e in sorted(trade.entries, key=lambda x: x.sequence)
                ],
                "snapshot": {
                    "weighted_avg": str(snapshot.weighted_avg_entry) if snapshot else "0",
                    "total_size": str(snapshot.total_size) if snapshot else "0",
                    "remaining_size": str(snapshot.remaining_size) if snapshot else "0",
                    "current_stop": str(snapshot.current_stop) if snapshot and snapshot.current_stop else None,
                    "locked_profit": str(snapshot.locked_profit) if snapshot else "0",
                    "booked_pnl": str(snapshot.total_booked_pnl) if snapshot else "0"
                }
            }

    def cancel_trade(
        self,
        trade_id: str,
        reason: str = "Price never reached entry zone"
    ) -> Tuple[bool, str]:
        with self._transaction() as session:
            trade = self.repo.get_by_trade_id(trade_id, session)
            if not trade:
                return False, "Trade not found"

            if trade.status != TradeStatus.OPEN:
                return False, f"Cannot cancel {trade.status.value} trade"

            idem_key = make_idempotency_key("CANCEL", trade_id)
            if self.repo.check_idempotency(idem_key, session):
                return True, "Already processed"

            self.repo.update_trade_status(trade.id, TradeStatus.CANCELLED, session)

            event = TradeEvent(
                event_type=EventType.TRADE_CANCELLED,
                payload={"reason": reason},
                idempotency_key=idem_key
            )
            self.repo.insert_event(trade.id, event, session)

            logger.info(f"TRADE_CANCELLED: {trade_id}")
            return True, "Trade cancelled"

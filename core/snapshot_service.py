"""Snapshot Service – Rebuild trade snapshots from entries.
FIXED: Removed session.commit() - must be called within existing transaction.
"""

import logging
from typing import Optional
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.db import Database, TradeModel, TradeEntryModel, TradeSnapshotModel

logger = logging.getLogger(__name__)


class SnapshotService:
    """Service for rebuilding and managing trade snapshots."""

    def __init__(self, session: Optional[Session] = None):
        """
        Initialize with optional session.
        If session provided, use it (must be called within transaction).
        If not, create new session (for standalone use only).
        """
        self.db = Database()
        self.session = session

    def rebuild_snapshot(self, session: Session, trade_db_id: int) -> Optional[TradeSnapshotModel]:
        """
        Recalculate the snapshot for a given trade based on its entries.

        CRITICAL FIX: Must be called within an existing transaction.
        NO session.commit() here - caller manages transaction.
        """
        # Fetch trade
        trade = session.execute(
            select(TradeModel).where(TradeModel.id == trade_db_id)
        ).scalar_one_or_none()
        if not trade:
            logger.warning(f"Trade {trade_db_id} not found for snapshot rebuild")
            return None

        # Fetch all entries for this trade
        entries = session.execute(
            select(TradeEntryModel).where(TradeEntryModel.trade_id == trade_db_id)
        ).scalars().all()

        if not entries:
            # No entries – delete any existing snapshot
            session.execute(
                update(TradeSnapshotModel)
                .where(TradeSnapshotModel.trade_id == trade_db_id)
                .values(weighted_avg_entry=0, total_size=0, remaining_size=0)
            )
            # CRITICAL FIX: No commit here - caller manages transaction
            return None

        # Use Decimal for precise calculations (database uses DECIMAL)
        total_size = Decimal("0")
        remaining_size = Decimal("0")
        weighted_sum = Decimal("0")

        for entry in entries:
            entry_price = Decimal(str(entry.entry_price))
            size = Decimal(str(entry.size))
            closed = Decimal(str(entry.closed_size)) if entry.closed_size else Decimal("0")
            remaining = size - closed

            total_size += size
            remaining_size += remaining
            weighted_sum += entry_price * remaining

        weighted_avg = weighted_sum / remaining_size if remaining_size > 0 else Decimal("0")

        # Check if snapshot already exists
        snapshot = session.execute(
            select(TradeSnapshotModel).where(TradeSnapshotModel.trade_id == trade_db_id)
        ).scalar_one_or_none()

        if snapshot:
            # Update existing snapshot
            session.execute(
                update(TradeSnapshotModel)
                .where(TradeSnapshotModel.trade_id == trade_db_id)
                .values(
                    weighted_avg_entry=float(weighted_avg),
                    total_size=float(total_size),
                    remaining_size=float(remaining_size),
                )
            )
            # Refresh snapshot from DB to return updated object
            snapshot = session.execute(
                select(TradeSnapshotModel).where(TradeSnapshotModel.trade_id == trade_db_id)
            ).scalar_one()
        else:
            # Create new snapshot
            snapshot = TradeSnapshotModel(
                trade_id=trade_db_id,
                weighted_avg_entry=float(weighted_avg),
                total_size=float(total_size),
                remaining_size=float(remaining_size),
                current_stop=float(trade.stop_loss) if trade.stop_loss else None,
                current_target=float(trade.target) if trade.target else None,
                locked_profit=0.0,
                total_booked_pnl=0.0,
            )
            session.add(snapshot)
            session.flush()

        # CRITICAL FIX: No commit here - caller manages transaction
        return snapshot


_snapshot_service = None


def get_snapshot_service() -> SnapshotService:
    global _snapshot_service
    if _snapshot_service is None:
        _snapshot_service = SnapshotService()
    return _snapshot_service

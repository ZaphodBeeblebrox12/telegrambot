"""
Repository Layer
"""

import logging
from contextlib import contextmanager
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .db import (
    TradeModel, TradeEntryModel, TradeEventModel, 
    TradeSnapshotModel, Database
)
from .models import Trade, TradeEntry, TradeEvent, TradeSnapshot, TradeStatus, EntryType, EventType

logger = logging.getLogger(__name__)


class TradeRepository:
    def __init__(self, db: Database):
        self.db = db

    @contextmanager
    def session(self):
        session = self.db.get_session()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Transaction rolled back: {e}")
            raise
        finally:
            session.close()

    def get_by_trade_id(self, trade_id: str, session: Session) -> Optional[Trade]:
        stmt = select(TradeModel).where(TradeModel.trade_id == trade_id)
        model = session.execute(stmt).scalar_one_or_none()
        return self._to_domain(model) if model else None

    def check_idempotency(self, idempotency_key: str, session: Session) -> bool:
        if not idempotency_key:
            return False
        stmt = select(TradeEventModel.id).where(
            TradeEventModel.idempotency_key == idempotency_key
        )
        return session.execute(stmt).scalar_one_or_none() is not None

    def create_trade(self, trade: Trade, initial_entry: TradeEntry, session: Session) -> Trade:
        model = TradeModel(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            side=trade.side,
            asset_class=trade.asset_class,
            status=trade.status.value
        )
        session.add(model)
        session.flush()

        entry_model = TradeEntryModel(
            trade_id=model.id,
            sequence=1,
            entry_price=initial_entry.entry_price,
            size=initial_entry.size,
            closed_size=Decimal("0"),
            entry_type=EntryType.INITIAL.value
        )
        session.add(entry_model)
        session.flush()

        trade.id = model.id
        trade.entries[0].id = entry_model.id
        trade.entries[0].sequence = 1

        return trade

    def add_entry(self, trade_id: int, entry: TradeEntry, session: Session) -> TradeEntry:
        stmt = select(func.max(TradeEntryModel.sequence)).where(
            TradeEntryModel.trade_id == trade_id
        )
        next_seq = (session.execute(stmt).scalar() or 0) + 1

        model = TradeEntryModel(
            trade_id=trade_id,
            sequence=next_seq,
            entry_price=entry.entry_price,
            size=entry.size,
            closed_size=Decimal("0"),
            entry_type=entry.entry_type.value
        )
        session.add(model)
        session.flush()

        entry.id = model.id
        entry.sequence = next_seq
        return entry

    def update_entry_closed_size(self, entry_id: int, closed_size: Decimal, session: Session) -> None:
        model = session.get(TradeEntryModel, entry_id)
        if model:
            model.closed_size = closed_size

    def update_trade_status(self, trade_id: int, status: TradeStatus, session: Session) -> None:
        model = session.get(TradeModel, trade_id)
        if model:
            model.status = status.value

    def insert_event(self, trade_id: int, event: TradeEvent, session: Session) -> Optional[TradeEvent]:
        if event.idempotency_key and self.check_idempotency(event.idempotency_key, session):
            return None

        stmt = select(func.max(TradeEventModel.sequence)).where(
            TradeEventModel.trade_id == trade_id
        )
        next_seq = (session.execute(stmt).scalar() or 0) + 1

        model = TradeEventModel(
            trade_id=trade_id,
            sequence=next_seq,
            event_type=event.event_type.value,
            payload=event.payload,
            idempotency_key=event.idempotency_key
        )
        session.add(model)
        session.flush()

        event.id = model.id
        event.sequence = next_seq
        return event

    def save_snapshot(self, trade_id: int, snapshot: TradeSnapshot, session: Session) -> None:
        existing = session.execute(
            select(TradeSnapshotModel).where(
                TradeSnapshotModel.trade_id == trade_id
            )
        ).scalar_one_or_none()

        if existing:
            existing.weighted_avg_entry = snapshot.weighted_avg_entry
            existing.total_size = snapshot.total_size
            existing.remaining_size = snapshot.remaining_size
            existing.current_stop = snapshot.current_stop
            existing.current_target = snapshot.current_target
            existing.locked_profit = snapshot.locked_profit
            existing.total_booked_pnl = snapshot.total_booked_pnl
            existing.snapshot_data = snapshot.snapshot_data
        else:
            model = TradeSnapshotModel(
                trade_id=trade_id,
                weighted_avg_entry=snapshot.weighted_avg_entry,
                total_size=snapshot.total_size,
                remaining_size=snapshot.remaining_size,
                current_stop=snapshot.current_stop,
                current_target=snapshot.current_target,
                locked_profit=snapshot.locked_profit,
                total_booked_pnl=snapshot.total_booked_pnl,
                snapshot_data=snapshot.snapshot_data
            )
            session.add(model)

    def get_snapshot(self, trade_id: int, session: Session) -> Optional[TradeSnapshot]:
        model = session.execute(
            select(TradeSnapshotModel).where(
                TradeSnapshotModel.trade_id == trade_id
            )
        ).scalar_one_or_none()

        if not model:
            return None

        return TradeSnapshot(
            weighted_avg_entry=model.weighted_avg_entry,
            total_size=model.total_size,
            remaining_size=model.remaining_size,
            current_stop=model.current_stop,
            current_target=model.current_target,
            locked_profit=model.locked_profit,
            total_booked_pnl=model.total_booked_pnl,
            snapshot_data=model.snapshot_data,
            updated_at=model.updated_at
        )

    def _to_domain(self, model: TradeModel) -> Trade:
        return Trade(
            id=model.id,
            trade_id=model.trade_id,
            symbol=model.symbol,
            side=model.side,
            asset_class=model.asset_class,
            status=TradeStatus(model.status),
            entries=[
                TradeEntry(
                    id=e.id,
                    sequence=e.sequence,
                    entry_price=e.entry_price,
                    size=e.size,
                    closed_size=e.closed_size,
                    entry_type=EntryType(e.entry_type),
                    created_at=e.created_at
                )
                for e in model.entries
            ],
            created_at=model.created_at,
            updated_at=model.updated_at
        )

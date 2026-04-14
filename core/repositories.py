"""
Repository layer - Data access
"""
from contextlib import contextmanager
from typing import Optional, List
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import TradeModel, TradeEntryModel, TradeEventModel, TradeSnapshotModel, Database
from .models import Trade, TradeEntry, TradeStatus, EntryType, TradeEvent, TradeSnapshot, EventType


class TradeRepository:
    def __init__(self, db: Database):
        self.db = db

    @contextmanager
    def session(self):
        session = self.db.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_by_trade_id(self, trade_id: str, session: Session) -> Optional[Trade]:
        stmt = select(TradeModel).where(TradeModel.trade_id == trade_id)
        model = session.execute(stmt).scalar_one_or_none()
        return self._to_domain(model) if model else None

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
            entry_price=initial_entry.entry_price,
            size=initial_entry.size,
            closed_size=initial_entry.closed_size,
            entry_type=initial_entry.entry_type.value,
            sequence=1
        )
        session.add(entry_model)
        session.flush()

        initial_entry.id = entry_model.id
        initial_entry.sequence = 1
        trade.id = model.id
        trade.entries = [initial_entry]

        return trade

    def add_entry(self, trade_id: int, entry: TradeEntry, session: Session):
        # Get next sequence
        stmt = select(TradeEntryModel).where(TradeEntryModel.trade_id == trade_id)
        existing = session.execute(stmt).scalars().all()
        next_seq = max([e.sequence for e in existing] + [0]) + 1

        model = TradeEntryModel(
            trade_id=trade_id,
            entry_price=entry.entry_price,
            size=entry.size,
            closed_size=entry.closed_size,
            entry_type=entry.entry_type.value,
            sequence=next_seq
        )
        session.add(model)
        session.flush()
        entry.id = model.id
        entry.sequence = next_seq

    def update_entry_closed_size(self, entry_id: int, closed_size: Decimal, session: Session):
        stmt = select(TradeEntryModel).where(TradeEntryModel.id == entry_id)
        model = session.execute(stmt).scalar_one()
        model.closed_size = closed_size

    def update_trade_status(self, trade_id: int, status: TradeStatus, session: Session):
        stmt = select(TradeModel).where(TradeModel.id == trade_id)
        model = session.execute(stmt).scalar_one()
        model.status = status.value

    def insert_event(self, trade_id: int, event: TradeEvent, session: Session):
        import json
        model = TradeEventModel(
            trade_id=trade_id,
            event_type=event.event_type.value,
            payload=json.dumps(event.payload),
            idempotency_key=event.idempotency_key
        )
        session.add(model)

    def check_idempotency(self, key: str, session: Session) -> bool:
        stmt = select(TradeEventModel).where(TradeEventModel.idempotency_key == key)
        return session.execute(stmt).scalar_one_or_none() is not None

    def save_snapshot(self, trade_id: int, snapshot: TradeSnapshot, session: Session):
        stmt = select(TradeSnapshotModel).where(TradeSnapshotModel.trade_id == trade_id)
        existing = session.execute(stmt).scalar_one_or_none()

        if existing:
            existing.weighted_avg_entry = snapshot.weighted_avg_entry
            existing.total_size = snapshot.total_size
            existing.remaining_size = snapshot.remaining_size
            existing.current_stop = snapshot.current_stop
            existing.current_target = snapshot.current_target
            existing.locked_profit = snapshot.locked_profit
            existing.total_booked_pnl = snapshot.total_booked_pnl
        else:
            model = TradeSnapshotModel(
                trade_id=trade_id,
                weighted_avg_entry=snapshot.weighted_avg_entry,
                total_size=snapshot.total_size,
                remaining_size=snapshot.remaining_size,
                current_stop=snapshot.current_stop,
                current_target=snapshot.current_target,
                locked_profit=snapshot.locked_profit,
                total_booked_pnl=snapshot.total_booked_pnl
            )
            session.add(model)

    def get_snapshot(self, trade_id: int, session: Session) -> Optional[TradeSnapshot]:
        stmt = select(TradeSnapshotModel).where(TradeSnapshotModel.trade_id == trade_id)
        model = session.execute(stmt).scalar_one_or_none()

        if not model:
            return None

        return TradeSnapshot(
            weighted_avg_entry=model.weighted_avg_entry,
            total_size=model.total_size,
            remaining_size=model.remaining_size,
            current_stop=model.current_stop,
            current_target=model.current_target,
            locked_profit=model.locked_profit,
            total_booked_pnl=model.total_booked_pnl
        )

    def _to_domain(self, model: TradeModel) -> Trade:
        entries = [
            TradeEntry(
                id=e.id,
                entry_price=e.entry_price,
                size=e.size,
                closed_size=e.closed_size,
                entry_type=EntryType(e.entry_type),
                sequence=e.sequence
            )
            for e in model.entries
        ]

        return Trade(
            id=model.id,
            trade_id=model.trade_id,
            symbol=model.symbol,
            side=model.side,
            asset_class=model.asset_class,
            entries=entries,
            status=TradeStatus(model.status),
            created_at=model.created_at
        )

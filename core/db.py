"""
Database Layer - SQLAlchemy Models
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, String, DateTime, Numeric, ForeignKey,
    JSON, create_engine, Index, UniqueConstraint
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column,
    relationship, Session, sessionmaker
)

class Base(DeclarativeBase):
    pass

class TradeModel(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    side: Mapped[str] = mapped_column(String(10))
    asset_class: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    entries: Mapped[list["TradeEntryModel"]] = relationship(
        back_populates="trade", cascade="all, delete-orphan",
        order_by="TradeEntryModel.sequence", lazy="selectin"
    )
    events: Mapped[list["TradeEventModel"]] = relationship(
        back_populates="trade", cascade="all, delete-orphan",
        order_by="TradeEventModel.sequence"
    )
    snapshot: Mapped[Optional["TradeSnapshotModel"]] = relationship(
        back_populates="trade", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('ix_trades_status', 'status'),
        Index('ix_trades_symbol_status', 'symbol', 'status'),
    )

class TradeEntryModel(Base):
    __tablename__ = "trade_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(19, 8))
    size: Mapped[Decimal] = mapped_column(Numeric(19, 8))
    closed_size: Mapped[Decimal] = mapped_column(Numeric(19, 8), default=Decimal("0"))
    entry_type: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    trade: Mapped["TradeModel"] = relationship(back_populates="entries")

    __table_args__ = (
        UniqueConstraint('trade_id', 'sequence', name='uix_trade_entry_sequence'),
        Index('ix_entries_trade_seq', 'trade_id', 'sequence'),
    )

class TradeEventModel(Base):
    __tablename__ = "trade_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128), unique=True, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    trade: Mapped["TradeModel"] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint('trade_id', 'sequence', name='uix_trade_event_sequence'),
        Index('ix_events_trade_type', 'trade_id', 'event_type'),
    )

class TradeSnapshotModel(Base):
    __tablename__ = "trade_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), unique=True, index=True
    )
    weighted_avg_entry: Mapped[Decimal] = mapped_column(Numeric(19, 8))
    total_size: Mapped[Decimal] = mapped_column(Numeric(19, 8))
    remaining_size: Mapped[Decimal] = mapped_column(Numeric(19, 8))
    current_stop: Mapped[Optional[Decimal]] = mapped_column(Numeric(19, 8), nullable=True)
    current_target: Mapped[Optional[Decimal]] = mapped_column(Numeric(19, 8), nullable=True)
    locked_profit: Mapped[Decimal] = mapped_column(Numeric(19, 8), default=Decimal("0"))
    total_booked_pnl: Mapped[Decimal] = mapped_column(Numeric(19, 8), default=Decimal("0"))
    snapshot_data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    trade: Mapped["TradeModel"] = relationship(back_populates="snapshot")

class MessageMappingModel(Base):
    __tablename__ = "message_mappings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(20))
    channel_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    message_id: Mapped[str] = mapped_column(String(50))
    parent_message_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    message_type: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('platform', 'channel_id', 'message_id',
                        name='uix_platform_message'),
        Index('ix_mappings_trade', 'trade_id'),
    )

class Database:
    def __init__(self, connection_string: str):
        self.engine = create_engine(
            connection_string,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=10,
            max_overflow=20,
            echo=False
        )
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

    def create_tables(self):
        Base.metadata.create_all(bind=self.engine)

    def get_session(self) -> Session:
        return self.SessionLocal()

"""
Database layer - SQLAlchemy models (PRODUCTION VERSION)
Drop-in replacement for core/db.py
"""
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime,
    Numeric, ForeignKey, Text, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

Base = declarative_base()

class TradeModel(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    trade_id = Column(String(20), unique=True, nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    asset_class = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="open")
    target = Column(Numeric(20, 8))
    stop_loss = Column(Numeric(20, 8))
    created_at = Column(DateTime, default=datetime.utcnow)

    entries = relationship("TradeEntryModel", back_populates="trade", cascade="all, delete-orphan")
    events = relationship("TradeEventModel", back_populates="trade", cascade="all, delete-orphan")
    snapshot = relationship("TradeSnapshotModel", back_populates="trade", uselist=False, cascade="all, delete-orphan")
    message_mappings = relationship("MessageMappingModel", back_populates="trade", cascade="all, delete-orphan")

class TradeEntryModel(Base):
    __tablename__ = "trade_entries"

    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    size = Column(Numeric(20, 8), nullable=False)
    closed_size = Column(Numeric(20, 8), default=0)
    entry_type = Column(String(20), nullable=False)
    sequence = Column(Integer, nullable=False)

    trade = relationship("TradeModel", back_populates="entries")

class TradeEventModel(Base):
    __tablename__ = "trade_events"

    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)
    event_type = Column(String(50), nullable=False)
    payload = Column(Text)
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    trade = relationship("TradeModel", back_populates="events")

class TradeSnapshotModel(Base):
    __tablename__ = "trade_snapshots"

    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), unique=True, nullable=False)
    weighted_avg_entry = Column(Numeric(20, 8), nullable=False)
    total_size = Column(Numeric(20, 8), nullable=False)
    remaining_size = Column(Numeric(20, 8), nullable=False)
    current_stop = Column(Numeric(20, 8))
    current_target = Column(Numeric(20, 8))
    locked_profit = Column(Numeric(20, 8), default=0)
    total_booked_pnl = Column(Numeric(20, 8), default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    trade = relationship("TradeModel", back_populates="snapshot")

class MessageMappingModel(Base):
    __tablename__ = "message_mappings"

    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    message_id = Column(String(100), nullable=False, index=True)
    channel_id = Column(String(100))
    message_type = Column(String(50), nullable=False)
    parent_tg_msg_id = Column(String(100))
    parent_main_msg_id = Column(String(100))
    reply_to_message_id = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    trade = relationship("TradeModel", back_populates="message_mappings")

class OutboxMessageModel(Base):
    __tablename__ = "outbox_messages"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(50), unique=True, nullable=False, index=True)
    destination = Column(String(50), nullable=False)
    channel_id = Column(String(100))
    message_type = Column(String(50), nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)
    error = Column(Text)

class Database:
    def __init__(self, connection_string: str = None):
        # Support env var or default to sqlite
        if connection_string is None:
            connection_string = os.getenv(
                "DATABASE_URL",
                os.getenv("DB_CONNECTION", "sqlite:///trading_bot.db")
            )

        self.engine = create_engine(
            connection_string,
            pool_pre_ping=True,
            pool_recycle=3600
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def get_session(self):
        return self.Session()

# Backward compatibility - keep existing interface
_get_db = None

def get_db():
    global _get_db
    if _get_db is None:
        _get_db = Database()
    return _get_db

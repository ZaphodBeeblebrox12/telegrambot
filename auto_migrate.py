"""
Auto-migration module - Run on bot startup
Creates tables and migrates existing data automatically.
"""
import logging
from sqlalchemy import create_engine, inspect, select
from core.db import Base, Database, TradeModel, TradeEntryModel, TradeSnapshotModel
from core.repositories import RepositoryFactory

logger = logging.getLogger(__name__)

def ensure_tables_exist(connection_string: str = None):
    """Ensure all tables exist (idempotent)"""
    db = Database(connection_string)
    engine = db.engine

    # Create all tables
    Base.metadata.create_all(engine)
    logger.info("✓ Database tables ensured")
    return db

def auto_migrate_legacy_trades():
    """
    Automatically migrate trades from old repository to new SQL schema.
    Safe to run multiple times (skips already migrated).
    """
    db = Database()
    session = db.get_session()

    try:
        # Get old repo (uses json file)
        old_repo = RepositoryFactory.get_trade_repository()

        # Get all trades from old storage
        trades = old_repo.get_all()

        migrated = 0
        skipped = 0

        for trade in trades:
            # Check if already migrated
            existing = session.execute(
                select(TradeModel).where(TradeModel.trade_id == trade.trade_id)
            ).scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            # Create trade
            trade_model = TradeModel(
                trade_id=trade.trade_id,
                symbol=trade.symbol,
                side=trade.side,
                asset_class=trade.asset_class,
                status=trade.status.value,
                target=trade.target,
                stop_loss=trade.stop_loss
            )
            session.add(trade_model)
            session.flush()

            # Create entries
            for i, entry in enumerate(trade.entries):
                entry_model = TradeEntryModel(
                    trade_id=trade_model.id,
                    entry_price=entry.entry_price,
                    size=entry.size,
                    closed_size=entry.closed_size,
                    entry_type=entry.type.value,
                    sequence=i + 1
                )
                session.add(entry_model)

            # Create snapshot
            weighted_avg = trade.weighted_avg_entry
            total_size = sum(e.size for e in trade.entries)
            remaining = sum(e.remaining_size for e in trade.entries)

            snapshot = TradeSnapshotModel(
                trade_id=trade_model.id,
                weighted_avg_entry=weighted_avg,
                total_size=total_size,
                remaining_size=remaining,
                current_stop=trade.current_stop,
                current_target=trade.target,
                locked_profit=0.0,
                total_booked_pnl=sum(f.booked_pnl for f in trade.fifo_closes) if trade.fifo_closes else 0.0
            )
            session.add(snapshot)

            migrated += 1

            # Commit every 10 trades to avoid large transactions
            if migrated % 10 == 0:
                session.commit()
                logger.info(f"  Migrated batch: {migrated} trades...")

        session.commit()

        if migrated > 0:
            logger.info(f"✓ Migration complete: {migrated} migrated, {skipped} already exist")
        else:
            logger.info(f"✓ No migrations needed ({skipped} trades already in DB)")

    except Exception as e:
        session.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        session.close()

def setup_production_database(connection_string: str = None):
    """
    One-call setup for production database.
    Run this in main.py before starting the bot.
    """
    logger.info("Setting up production database...")

    # 1. Ensure tables exist
    ensure_tables_exist(connection_string)

    # 2. Migrate legacy data
    auto_migrate_legacy_trades()

    logger.info("✓ Production database ready")

# Backward compatibility
migrate_existing_trades = auto_migrate_legacy_trades

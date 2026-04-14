"""Trading Bot - Main Entry Point"""
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

from orchestration.orchestrator import get_orchestrator
from core.repositories import RepositoryFactory
from core.db import Database

def setup_database():
    """Initialize database tables"""
    db = RepositoryFactory.get_database()
    print("✅ Database initialized")
    return db

def verify_sql_repositories():
    """Verify SQL repositories are active"""
    trade_repo = RepositoryFactory.get_trade_repository()
    mapping_repo = RepositoryFactory.get_mapping_repository()

    from core.repositories import SQLTradeRepository, SQLMessageMappingRepository

    assert isinstance(trade_repo, SQLTradeRepository), "Trade repo must be SQL-based"
    assert isinstance(mapping_repo, SQLMessageMappingRepository), "Mapping repo must be SQL-based"

    print("✅ SQL repositories verified")

def main():
    print("=" * 60)
    print("Trading Bot - Production Ready (Option A)")
    print("=" * 60)

    setup_database()
    verify_sql_repositories()

    orchestrator = get_orchestrator()

    status = orchestrator.get_system_status()
    print(f"\n📊 System Status:")
    print(f"   Config Version: {status['config_version']}")
    print(f"   Handlers: {', '.join(status['handlers_registered'])}")
    print(f"   Open Trades: {status['trade_stats']['open_trades']}")

    print("\n" + "=" * 60)
    print("System ready.")
    print("=" * 60)

if __name__ == "__main__":
    main()

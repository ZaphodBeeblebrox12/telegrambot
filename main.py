"""
Main Entry Point - Trading Bot

Usage:
    python main.py

Environment Variables:
    TELEGRAM_BOT_TOKEN - Bot token from @BotFather
    DATABASE_URL - MySQL/PostgreSQL connection string
    CONFIG_PATH - Path to config.json (default: config/config.json)
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point"""
    logger.info("Starting Trading Bot...")

    # Get configuration
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        print("Please set TELEGRAM_BOT_TOKEN environment variable")
        sys.exit(1)

    database_url = os.getenv("DATABASE_URL", "mysql+pymysql://user:pass@localhost/trading")
    config_path = os.getenv("CONFIG_PATH", "config/config.json")

    logger.info(f"Database: {database_url.split('@')[1] if '@' in database_url else 'localhost'}")
    logger.info(f"Config: {config_path}")

    # Initialize database
    from core.db import Database
    db = Database(database_url)
    db.create_tables()
    logger.info("Database tables created")

    # Initialize and run bot
    from bot.telegram_bot import TradingBot
    bot = TradingBot(
        token=token,
        config_path=config_path,
        db=db
    )

    logger.info("Bot initialized, starting polling...")
    bot.run()


if __name__ == "__main__":
    main()

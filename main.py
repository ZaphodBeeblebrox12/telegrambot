#!/usr/bin/env python3
"""
Main Entry Point - Trading Bot

Usage:
    python main.py

Environment Variables:
    TELEGRAM_BOT_TOKEN - Bot token from @BotFather
    DATABASE_URL - MySQL/PostgreSQL connection string
    CONFIG_PATH - Path to config.json (default: config/config.json)
    LOG_LEVEL - Logging level (default: INFO)

Pipeline:
    IMAGE / COMMAND → OCR → CONFIG → SERVICE → DB → FORMAT → TELEGRAM
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
def setup_logging():
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('trading_bot.log')
        ]
    )

logger = logging.getLogger(__name__)

def validate_config():
    """Validate required configuration"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        print("Error: Please set TELEGRAM_BOT_TOKEN environment variable")
        print("Get your token from @BotFather on Telegram")
        return False
    return True

def main():
    """Main entry point"""
    setup_logging()
    logger.info("=" * 50)
    logger.info("Starting Trading Bot...")
    logger.info("=" * 50)

    if not validate_config():
        sys.exit(1)

    # Get configuration
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    database_url = os.getenv(
        "DATABASE_URL", 
        "mysql+pymysql://user:pass@localhost/trading"
    )
    config_path = os.getenv("CONFIG_PATH", "config/config.json")

    # Mask sensitive info in logs
    masked_url = database_url
    if '@' in masked_url:
        masked_url = f"***@{masked_url.split('@')[1]}"

    logger.info(f"Database: {masked_url}")
    logger.info(f"Config: {config_path}")
    logger.info(f"Log Level: {os.getenv('LOG_LEVEL', 'INFO')}")

    try:
        # Initialize database
        logger.info("Initializing database...")
        from core.db import Database
        db = Database(database_url)
        db.create_tables()
        logger.info("Database tables verified/created")

        # Initialize and run bot
        logger.info("Initializing Telegram bot...")
        from bot.telegram_bot import TradingBot

        bot = TradingBot(
            token=token,
            config_path=config_path,
            db=db
        )

        logger.info("Bot initialized, starting polling...")
        logger.info("Press Ctrl+C to stop")

        # Run (blocking)
        bot.run()

    except KeyboardInterrupt:
        logger.info("Received stop signal, shutting down...")
    except Exception as e:
        logger.exception("Fatal error occurred")
        sys.exit(1)

if __name__ == "__main__":
    main()

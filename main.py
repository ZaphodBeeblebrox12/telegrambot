"""
Trading Bot - Main Entry Point

Config-driven architecture:
CONFIG → ROUTER → EXECUTOR → SERVICE → FORMATTER → MAPPING → TELEGRAM
"""
import os
import logging
from dotenv import load_dotenv

from core.db import Database
from bot.telegram_bot import TradingBot

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    config_path = os.path.join(os.path.dirname(__file__), "config", "config.json")

    db = Database()
    bot = TradingBot(token=token, config_path=config_path, db=db)

    logger.info("Starting bot...")
    bot.run()


if __name__ == "__main__":
    main()

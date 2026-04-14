"""
Trading Bot - Main Entry Point

Config-driven architecture:
CONFIG → ROUTER → EXECUTOR → SERVICE → FORMATTER → MAPPING → PUBLISHER

10 Critical Fixes Implemented:
1. OCR System uses config.ocr_processing
2. Complete Execution Map for all message types
3. Config-driven Execution Engine
4. Message Mapping System aligned with config
5. Threading System from config.reply_nesting
6. Formatter with NO hardcoding
7. Price + Leverage from config
8. Destination Routing from config.destinations
9. Trade Ledger aligned with config
10. Repository pattern for PostgreSQL migration
"""
import os
import sys
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from bot.telegram_bot import TradingBot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    try:
        # Validate config exists
        config_path = project_root / "config" / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        logger.info("Starting Config-Driven Trading Bot v2.0...")
        logger.info(f"Config: {config_path}")

        # Initialize and run bot
        bot = TradingBot()
        bot.run()

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()

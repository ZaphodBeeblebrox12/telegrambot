"""
Orchestrator - Main Pipeline
IMAGE / COMMAND → OCR → CONFIG → SERVICE → DB → FORMAT → TELEGRAM
"""

import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

from core.services import TradeService
from .config_executor import ConfigExecutor, ExecutionContext
from .command_router import CommandRouter
from .formatter import MessageFormatter

logger = logging.getLogger(__name__)

@dataclass
class PipelineResult:
    success: bool
    message_type: Optional[str]
    telegram_text: Optional[str]
    twitter_text: Optional[str]
    trade_id: Optional[str]
    service_result: Optional[Dict[str, Any]]
    error: Optional[str] = None

class TradingPipeline:
    """
    Main trading pipeline coordinating all components.

    Flow:
    1. Image/Command Input
    2. OCR Analysis (for images)
    3. Config Lookup
    4. Service Execution
    5. DB Persistence
    6. Message Formatting
    7. Platform Output
    """

    def __init__(self, config_path: str, trade_service: TradeService):
        self.config_path = config_path
        self.service = trade_service
        self.router = CommandRouter(config_path)
        self.executor = ConfigExecutor(config_path, trade_service)
        self.formatter = MessageFormatter(config_path)
        logger.info("TradingPipeline initialized")

    def process_command(
        self,
        command_text: str,
        trade_id: str,
        symbol: str,
        **kwargs
    ) -> PipelineResult:
        """Process an update command through the pipeline."""
        logger.info(f"Processing command: {command_text} for trade {trade_id}")

        # 1. Route command
        parsed = self.router.parse(command_text)
        if not parsed:
            logger.warning(f"Unknown command: {command_text}")
            return PipelineResult(
                success=False,
                message_type=None,
                telegram_text=None,
                twitter_text=None,
                trade_id=trade_id,
                service_result=None,
                error=f"Unknown command: {command_text}"
            )

        # 2. Build execution context
        ctx = ExecutionContext(
            message_type=parsed.message_type,
            command=parsed.command,
            params=parsed.params,
            trade_id=trade_id,
            symbol=symbol,
            price=parsed.params.get("price"),
            percentage=parsed.params.get("percentage"),
            note_text=parsed.params.get("note_text")
        )

        # 3. Execute through service
        success, service_result = self.executor.execute(ctx)

        if not success:
            logger.error(f"Execution failed: {service_result.get('error', 'Unknown')}")
            return PipelineResult(
                success=False,
                message_type=parsed.message_type,
                telegram_text=None,
                twitter_text=None,
                trade_id=trade_id,
                service_result=service_result,
                error=service_result.get("error", "Execution failed")
            )

        # 4. Format output
        telegram_text = self.formatter.format(
            parsed.message_type, "telegram", service_result
        )

        twitter_text = self.formatter.format(
            parsed.message_type, "twitter", service_result
        )

        logger.info(f"Command processed successfully: {trade_id}")

        return PipelineResult(
            success=True,
            message_type=parsed.message_type,
            telegram_text=telegram_text,
            twitter_text=twitter_text,
            trade_id=service_result.get("trade_id", trade_id),
            service_result=service_result
        )

    def process_setup(
        self,
        ocr_data: Dict[str, Any],
        **kwargs
    ) -> PipelineResult:
        """Process a new trade setup from OCR data."""
        logger.info(f"Processing trade setup: {ocr_data.get('symbol')}")

        # 1. Build execution context from OCR data
        ctx = ExecutionContext(
            message_type="trade_setup",
            command=None,
            params=ocr_data,
            symbol=ocr_data.get("symbol"),
            side=ocr_data.get("side"),
            asset_class=ocr_data.get("asset_class")
        )

        # 2. Execute through service
        success, service_result = self.executor.execute(ctx)

        if not success:
            logger.error(f"Setup failed: {service_result.get('error', 'Unknown')}")
            return PipelineResult(
                success=False,
                message_type=None,
                telegram_text=None,
                twitter_text=None,
                trade_id=None,
                service_result=service_result,
                error=service_result.get("error", "Setup failed")
            )

        trade_id = service_result.get("trade_id")

        # 3. Format output
        telegram_text = self.formatter.format(
            "trade_setup", "telegram", service_result
        )

        twitter_text = self.formatter.format(
            "trade_setup", "twitter", service_result
        )

        logger.info(f"Trade setup created: {trade_id}")

        return PipelineResult(
            success=True,
            message_type="trade_setup",
            telegram_text=telegram_text,
            twitter_text=twitter_text,
            trade_id=trade_id,
            service_result=service_result
        )

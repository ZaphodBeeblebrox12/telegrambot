"""
Orchestrator - Main pipeline coordinator

Coordinates: Router → Executor → Formatter → Telegram
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

from core.services import TradeService
from orchestration.command_router import CommandRouter
from orchestration.config_executor import ConfigExecutor, ExecutionContext
from orchestration.formatter import MessageFormatter

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    success: bool
    trade_id: Optional[str]
    telegram_text: str
    twitter_text: Optional[str]
    message_type: Optional[str]
    error: Optional[str] = None
    data: Optional[Dict] = None


class TradingPipeline:
    """Main orchestration pipeline - config-driven."""

    def __init__(self, config_path: str, trade_service: TradeService):
        self.config_path = config_path
        self.router = CommandRouter(config_path)
        self.executor = ConfigExecutor(config_path, trade_service)
        self.formatter = MessageFormatter(config_path)
        logger.info("TradingPipeline initialized")

    def process_setup(self, ocr_result: Dict[str, Any]) -> PipelineResult:
        """Process new trade setup from OCR."""
        try:
            if not ocr_result.get("setup_found"):
                return PipelineResult(
                    success=False,
                    trade_id=None,
                    telegram_text="",
                    twitter_text=None,
                    message_type=None,
                    error="No setup found in image"
                )

            # Build execution context
            ctx = ExecutionContext(
                message_type="trade_setup",
                command=None,
                params=ocr_result,
                symbol=ocr_result.get("symbol"),
                side=ocr_result.get("side"),
                asset_class=ocr_result.get("asset_class", "FOREX")
            )

            # Execute through config-driven executor
            success, result = self.executor.execute(ctx)

            if not success:
                return PipelineResult(
                    success=False,
                    trade_id=None,
                    telegram_text="",
                    twitter_text=None,
                    message_type="trade_setup",
                    error=result.get("error", "Unknown error")
                )

            # Format for each platform using config
            telegram_text = self.formatter.format(
                "trade_setup", "telegram", result
            )
            twitter_text = self.formatter.format(
                "trade_setup", "twitter", result
            )

            return PipelineResult(
                success=True,
                trade_id=result.get("trade_id"),
                telegram_text=telegram_text,
                twitter_text=twitter_text,
                message_type="trade_setup",
                data=result
            )

        except Exception as e:
            logger.exception("Pipeline error in process_setup")
            return PipelineResult(
                success=False,
                trade_id=None,
                telegram_text="",
                twitter_text=None,
                message_type=None,
                error=str(e)
            )

    def process_command(
        self,
        command_text: str,
        trade_id: str,
        symbol: str
    ) -> PipelineResult:
        """Process command update through config-driven pipeline."""
        try:
            # Parse command using config-driven router
            parsed = self.router.parse(command_text)

            if not parsed:
                return PipelineResult(
                    success=False,
                    trade_id=trade_id,
                    telegram_text="",
                    twitter_text=None,
                    message_type=None,
                    error=f"Unknown command: {command_text}"
                )

            # Build execution context
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

            # Execute through config-driven executor
            success, result = self.executor.execute(ctx)

            if not success:
                return PipelineResult(
                    success=False,
                    trade_id=trade_id,
                    telegram_text="",
                    twitter_text=None,
                    message_type=parsed.message_type,
                    error=result.get("error", "Execution failed")
                )

            # Format for platforms using config
            telegram_text = self.formatter.format(
                parsed.message_type, "telegram", result
            )

            # Only format twitter if platform supports it
            twitter_text = None
            if self.formatter.is_platform_supported(parsed.message_type, "twitter"):
                twitter_text = self.formatter.format(
                    parsed.message_type, "twitter", result
                )

            return PipelineResult(
                success=True,
                trade_id=trade_id,
                telegram_text=telegram_text,
                twitter_text=twitter_text,
                message_type=parsed.message_type,
                data=result
            )

        except Exception as e:
            logger.exception("Pipeline error in process_command")
            return PipelineResult(
                success=False,
                trade_id=trade_id,
                telegram_text="",
                twitter_text=None,
                message_type=None,
                error=str(e)
            )

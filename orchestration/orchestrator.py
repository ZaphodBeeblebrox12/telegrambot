"""
Orchestrator - Main Pipeline
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
    def __init__(self, config_path: str, trade_service: TradeService):
        self.config_path = config_path
        self.service = trade_service
        self.router = CommandRouter(config_path)
        self.executor = ConfigExecutor(config_path, trade_service)
        self.formatter = MessageFormatter(config_path)

    def process_command(
        self,
        command_text: str,
        trade_id: str,
        symbol: str,
        **kwargs
    ) -> PipelineResult:

        parsed = self.router.parse(command_text)
        if not parsed:
            return PipelineResult(
                success=False,
                error=f"Unknown command: {command_text}"
            )

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

        success, service_result = self.executor.execute(ctx)

        if not success:
            return PipelineResult(
                success=False,
                message_type=parsed.message_type,
                error=service_result.get("error", "Execution failed")
            )

        telegram_text = self.formatter.format(
            parsed.message_type, "telegram", service_result
        )

        twitter_text = self.formatter.format(
            parsed.message_type, "twitter", service_result
        )

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

        ctx = ExecutionContext(
            message_type="trade_setup",
            command=None,
            params=ocr_data,
            symbol=ocr_data.get("symbol"),
            side=ocr_data.get("side"),
            asset_class=ocr_data.get("asset_class")
        )

        success, service_result = self.executor.execute(ctx)

        if not success:
            return PipelineResult(
                success=False,
                error=service_result.get("error", "Setup failed")
            )

        trade_id = service_result.get("trade_id")

        telegram_text = self.formatter.format(
            "trade_setup", "telegram", service_result
        )

        twitter_text = self.formatter.format(
            "trade_setup", "twitter", service_result
        )

        return PipelineResult(
            success=True,
            message_type="trade_setup",
            telegram_text=telegram_text,
            twitter_text=twitter_text,
            trade_id=trade_id,
            service_result=service_result
        )

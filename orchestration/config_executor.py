"""
Config Executor - Execute commands through TradeService
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Any, Tuple, Optional
import json

from core.services import TradeService

logger = logging.getLogger(__name__)

@dataclass
class ExecutionContext:
    message_type: str
    command: Optional[str]
    params: Dict[str, Any]
    symbol: Optional[str] = None
    trade_id: Optional[str] = None
    side: Optional[str] = None
    asset_class: Optional[str] = None
    price: Optional[str] = None
    percentage: Optional[str] = None
    note_text: Optional[str] = None


class ConfigExecutor:
    """Executes commands through the TradeService using config-driven execution_map."""

    def __init__(self, config_path: str, trade_service: TradeService):
        self.config_path = config_path
        self.service = trade_service
        self.execution_map = self._load_execution_map()
        logger.info("ConfigExecutor initialized with config-driven execution map")

    def _load_execution_map(self) -> Dict[str, str]:
        """Load execution_map from config file."""
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            execution_map = config.get("execution_map", {})
            logger.info(f"Loaded execution_map with {len(execution_map)} mappings")
            return execution_map
        except Exception as e:
            logger.error(f"Failed to load execution_map from config: {e}")
            return {}

    def execute(self, ctx: ExecutionContext) -> Tuple[bool, Dict[str, Any]]:
        """Execute command based on message_type using config-driven execution_map."""
        # Resolve service method from execution_map
        service_method_name = self.execution_map.get(ctx.message_type)

        if not service_method_name:
            return False, {"error": f"No execution mapping for message type: {ctx.message_type}"}

        # Dynamically resolve service method
        service_method = getattr(self.service, service_method_name, None)
        if not service_method:
            return False, {"error": f"Service method '{service_method_name}' not found for message type: {ctx.message_type}"}

        # Execute the resolved method
        return service_method(ctx)

    def _handle_trade_setup(self, ctx: ExecutionContext) -> Tuple[bool, Dict[str, Any]]:
        """Create new trade from OCR data."""
        try:
            params = ctx.params
            symbol = params.get("symbol")
            side = params.get("side")
            asset_class = params.get("asset_class", "FOREX")
            entry_price = Decimal(params.get("entry", "0"))
            target = Decimal(params.get("target")) if params.get("target") else None
            stop_loss = Decimal(params.get("stop_loss")) if params.get("stop_loss") else None

            if not all([symbol, side, entry_price]):
                return False, {"error": "Missing required fields: symbol, side, entry"}

            trade = self.service.create_trade(
                symbol=symbol,
                side=side,
                asset_class=asset_class,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target=target
            )

            return True, {
                "trade_id": trade.trade_id,
                "symbol": symbol,
                "side": side,
                "entry": str(entry_price),
                "target": str(target) if target else None,
                "stop_loss": str(stop_loss) if stop_loss else None
            }

        except Exception as e:
            logger.exception("Trade setup failed")
            return False, {"error": str(e)}

    def _handle_partial_close(self, ctx: ExecutionContext) -> Tuple[bool, Dict[str, Any]]:
        """Handle partial close command."""
        try:
            trade_id = ctx.trade_id
            exit_price = Decimal(ctx.price) if ctx.price else None
            percentage = Decimal(ctx.percentage) if ctx.percentage else Decimal("25")

            if not trade_id:
                return False, {"error": "Missing trade_id"}
            if not exit_price:
                return False, {"error": "Missing exit price"}

            success, result, msg = self.service.partial_close(
                trade_id=trade_id,
                close_percentage=percentage,
                exit_price=exit_price
            )

            if not success:
                return False, {"error": msg}

            # Format tree lines from FIFO result
            tree_lines = []
            if result:
                for detail in result.fifo:
                    tree_lines.append(
                        f"Exit {detail.entry_sequence}: "
                        f"@{detail.entry_price} × {detail.taken} "
                        f"→ PnL: {detail.pnl:.2f}"
                    )

            return True, {
                "trade_id": trade_id,
                "percentage": str(percentage),
                "exit_price": str(exit_price),
                "pnl": str(result.total_pnl) if result else "0",
                "tree_lines": "\n".join(tree_lines) if tree_lines else "",
                "fifo_result": result.to_tree_dict() if result else {}
            }

        except Exception as e:
            logger.exception("Partial close failed")
            return False, {"error": str(e)}

    def _handle_full_close(self, ctx: ExecutionContext) -> Tuple[bool, Dict[str, Any]]:
        """Handle full close command."""
        try:
            trade_id = ctx.trade_id
            exit_price = Decimal(ctx.price) if ctx.price else None

            if not trade_id:
                return False, {"error": "Missing trade_id"}
            if not exit_price:
                return False, {"error": "Missing exit price"}

            success, result, msg = self.service.full_close(
                trade_id=trade_id,
                exit_price=exit_price
            )

            if not success:
                return False, {"error": msg}

            return True, {
                "trade_id": trade_id,
                "exit_price": str(exit_price),
                "pnl": str(result.total_pnl) if result else "0",
                "reason": "manual"
            }

        except Exception as e:
            logger.exception("Full close failed")
            return False, {"error": str(e)}

    def _handle_trail_update(self, ctx: ExecutionContext) -> Tuple[bool, Dict[str, Any]]:
        """Handle trailing stop update."""
        try:
            trade_id = ctx.trade_id
            new_stop = Decimal(ctx.price) if ctx.price else None

            if not trade_id:
                return False, {"error": "Missing trade_id"}
            if not new_stop:
                return False, {"error": "Missing stop price"}

            success, msg = self.service.update_stop(
                trade_id=trade_id,
                new_stop=new_stop
            )

            if not success:
                return False, {"error": msg}

            return True, {
                "trade_id": trade_id,
                "new_stop": str(new_stop)
            }

        except Exception as e:
            logger.exception("Trail update failed")
            return False, {"error": str(e)}

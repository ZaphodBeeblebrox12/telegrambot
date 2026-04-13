"""
ConfigExecutor - Configuration-Driven Execution
"""

import json
import logging
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

from core.services import TradeService

logger = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    message_type: str
    command: Optional[str]
    params: Dict[str, Any]
    trade_id: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    asset_class: Optional[str] = None
    price: Optional[Decimal] = None
    percentage: Optional[Decimal] = None
    note_text: Optional[str] = None


class ConfigExecutor:
    def __init__(self, config_path: str, trade_service: TradeService):
        self.config = self._load_config(config_path)
        self.service = trade_service
        self.message_types = self.config.get("message_types", {})

        self._method_map = {
            "trade_setup": self._execute_trade_setup,
            "trail_update_specific": self._execute_trail_update,
            "partial_close_specific": self._execute_partial_close,
            "close_half_specific": self._execute_close_half,
            "trade_close_specific": self._execute_full_close,
            "target_hit_specific": self._execute_full_close,
            "stopped_out_specific": self._execute_full_close,
            "breakeven_specific": self._execute_full_close,
            "stop_update_specific": self._execute_stop_update,
            "target_update_specific": self._execute_target_update,
            "pyramid_update_specific": self._execute_pyramid,
            "trade_cancelled_specific": self._execute_cancel,
        }

    def _load_config(self, path: str) -> Dict[str, Any]:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def execute(self, context: ExecutionContext) -> Tuple[bool, Dict[str, Any]]:
        handler = self._method_map.get(context.message_type)
        if not handler:
            return False, {"error": f"No handler for {context.message_type}"}

        try:
            return handler(context)
        except Exception as e:
            logger.exception(f"Execution failed")
            return False, {"error": str(e)}

    def _execute_trade_setup(self, ctx: ExecutionContext) -> Tuple[bool, Dict]:
        if not ctx.params.get("setup_found"):
            return True, {"skipped": True}

        trade = self.service.create_trade(
            symbol=ctx.symbol or ctx.params.get("symbol"),
            side=ctx.side or ctx.params.get("side"),
            asset_class=ctx.asset_class or ctx.params.get("asset_class"),
            entry_price=Decimal(str(ctx.params.get("entry", 0))),
            stop_loss=Decimal(str(ctx.params.get("stop_loss", 0))) if ctx.params.get("stop_loss") else None,
            target=Decimal(str(ctx.params.get("target", 0))) if ctx.params.get("target") else None
        )

        return True, {"trade_id": trade.trade_id, "action": "create_trade"}

    def _execute_trail_update(self, ctx: ExecutionContext) -> Tuple[bool, Dict]:
        if not ctx.trade_id or not ctx.price:
            return False, {"error": "Missing trade_id or price"}

        success, msg = self.service.update_stop(ctx.trade_id, ctx.price)

        if success:
            status = self.service.get_trade_status(ctx.trade_id)
            return True, {
                "trade_id": ctx.trade_id,
                "action": "update_stop",
                "new_stop": str(ctx.price),
                "message": msg
            }
        return False, {"error": msg}

    def _execute_partial_close(self, ctx: ExecutionContext) -> Tuple[bool, Dict]:
        if not ctx.trade_id or not ctx.price:
            return False, {"error": "Missing trade_id or price"}

        percentage = ctx.percentage or Decimal("25")

        success, result, msg = self.service.partial_close(
            ctx.trade_id, percentage, ctx.price
        )

        if success and result:
            return True, {
                "trade_id": ctx.trade_id,
                "action": "partial_close",
                "percentage": str(percentage),
                "pnl": str(result.total_pnl),
                "fifo_result": result.to_tree_dict()
            }
        return False, {"error": msg}

    def _execute_close_half(self, ctx: ExecutionContext) -> Tuple[bool, Dict]:
        if not ctx.trade_id or not ctx.price:
            return False, {"error": "Missing trade_id or price"}

        success, result, msg = self.service.partial_close(
            ctx.trade_id, Decimal("50"), ctx.price
        )

        if success and result:
            return True, {
                "trade_id": ctx.trade_id,
                "action": "close_half",
                "percentage": "50",
                "pnl": str(result.total_pnl),
                "fifo_result": result.to_tree_dict()
            }
        return False, {"error": msg}

    def _execute_full_close(self, ctx: ExecutionContext) -> Tuple[bool, Dict]:
        if not ctx.trade_id or not ctx.price:
            return False, {"error": "Missing trade_id or price"}

        reason_map = {
            "trade_close_specific": "manual",
            "target_hit_specific": "target",
            "stopped_out_specific": "stop_loss",
            "breakeven_specific": "breakeven"
        }
        reason = reason_map.get(ctx.message_type, "manual")

        success, result, msg = self.service.full_close(
            ctx.trade_id, ctx.price, reason
        )

        if success and result:
            return True, {
                "trade_id": ctx.trade_id,
                "action": "full_close",
                "reason": reason,
                "pnl": str(result.total_pnl)
            }
        return False, {"error": msg}

    def _execute_stop_update(self, ctx: ExecutionContext) -> Tuple[bool, Dict]:
        if not ctx.trade_id or not ctx.price:
            return False, {"error": "Missing trade_id or price"}

        success, msg = self.service.update_stop(ctx.trade_id, ctx.price)

        if success:
            return True, {
                "trade_id": ctx.trade_id,
                "action": "update_stop",
                "new_stop": str(ctx.price)
            }
        return False, {"error": msg}

    def _execute_target_update(self, ctx: ExecutionContext) -> Tuple[bool, Dict]:
        logger.info(f"Target update for {ctx.trade_id}: {ctx.price}")
        return True, {
            "trade_id": ctx.trade_id,
            "action": "update_target",
            "new_target": str(ctx.price) if ctx.price else None
        }

    def _execute_pyramid(self, ctx: ExecutionContext) -> Tuple[bool, Dict]:
        if not ctx.trade_id or not ctx.price:
            return False, {"error": "Missing trade_id or price"}

        size_pct = ctx.percentage or Decimal("100")

        success, msg = self.service.pyramid_add(
            ctx.trade_id, ctx.price, size_pct
        )

        if success:
            status = self.service.get_trade_status(ctx.trade_id)
            return True, {
                "trade_id": ctx.trade_id,
                "action": "pyramid_add",
                "entry_price": str(ctx.price),
                "size_percentage": str(size_pct)
            }
        return False, {"error": msg}

    def _execute_cancel(self, ctx: ExecutionContext) -> Tuple[bool, Dict]:
        if not ctx.trade_id:
            return False, {"error": "Missing trade_id"}

        reason = ctx.note_text or "Price never reached entry zone"

        success, msg = self.service.cancel_trade(ctx.trade_id, reason)

        if success:
            return True, {
                "trade_id": ctx.trade_id,
                "action": "cancel_trade",
                "reason": reason
            }
        return False, {"error": msg}

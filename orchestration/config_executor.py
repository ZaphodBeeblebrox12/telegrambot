"""Config-driven Command Executor - Dynamic handler registration"""
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
from datetime import datetime

from config.config_loader import config
from core.models import Trade, ParsedCommand, TradeStatus, EntryType
from core.services import get_trade_service
from core.fifo import get_fifo_manager
from core.id_generator import get_id_generator

@dataclass
class ExecutionResult:
    """Result of command execution"""
    success: bool
    trade: Optional[Trade]
    message_type: Optional[str]
    variables: Dict[str, Any]
    error: Optional[str] = None

class ConfigExecutor:
    """Executes commands based on config with dynamic handler registration"""

    def __init__(self):
        self.cfg = config
        self.trade_service = get_trade_service()
        self.fifo_mgr = get_fifo_manager()
        self.id_gen = get_id_generator()
        self.handlers: Dict[str, Callable] = {}
        self._register_handlers()

    def _register_handlers(self):
        """Register command handlers from config"""
        if not self.cfg.commands:
            return

        update_config = self.cfg.commands.get('/update')
        if update_config and update_config.command_mapping:
            for cmd, mapping in update_config.command_mapping.items():
                handler_name = f"_handle_{cmd.lower()}"
                if hasattr(self, handler_name):
                    self.handlers[cmd] = getattr(self, handler_name)

    async def execute(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Execute parsed command"""
        handler = self.handlers.get(parsed.subcommand)

        if not handler:
            return ExecutionResult(
                success=False,
                trade=trade,
                message_type=None,
                variables={},
                error=f"No handler for command: {parsed.subcommand}"
            )

        try:
            return await handler(trade, parsed)
        except Exception as e:
            return ExecutionResult(
                success=False,
                trade=trade,
                message_type=None,
                variables={},
                error=str(e)
            )

    async def _handle_trail(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle TRAIL command"""
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for TRAIL"
            )

        updated = self.trade_service.update_trade_status(
            trade.trade_id,
            TradeStatus.OPEN,
            current_stop=parsed.price
        )

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='trail_update_specific',
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_closed(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle CLOSED command"""
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for CLOSED"
            )

        # Execute FIFO close for 100%
        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, parsed.price, 100.0
        )

        updated = self.trade_service.update_trade_status(
            trade.trade_id, TradeStatus.CLOSED
        )

        entry_price = trade.weighted_avg_entry
        price_change = parsed.price - entry_price
        position_return = (price_change / entry_price * 100) if entry_price else 0

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='trade_close_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': '100',
                'price': parsed.price,
                'entry': entry_price,
                'price_change': f"{position_return:+.2f}%",
                'position_return': f"{position_return * trade.leverage_multiplier:+.2f}%",
                'status': 'CLOSED',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_partial(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle PARTIAL command with FIFO"""
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for PARTIAL"
            )

        percentage = parsed.percentage or 25.0

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, parsed.price, percentage
        )

        if not close_result:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Failed to execute partial close"
            )

        # Format FIFO tree
        tree_lines = self.fifo_mgr.format_fifo_tree(
            entries=close_result['trade'].entries,
            close_details=close_result['close_details'],
            symbol=trade.symbol,
            header=f"🔹 PARTIAL CLOSE ({percentage}%) | {trade.symbol}",
            booked_pnl=close_result['booked_pnl'],
            remaining_size=close_result['remaining_size'],
            weighted_avg=close_result['new_weighted_avg'],
            current_stop=trade.current_stop or 0,
            leverage=trade.leverage_multiplier,
            platform='telegram'
        )

        return ExecutionResult(
            success=True,
            trade=close_result['trade'],
            message_type='partial_close_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': percentage,
                'price': parsed.price,
                'tree_lines': tree_lines,
                'booked_pnl': f"{close_result['booked_pnl']:+.2f}",
                'remaining_size': close_result['remaining_size'],
                'weighted_avg': close_result['new_weighted_avg'],
                'current_stop': trade.current_stop or 0,
                'status': 'OPEN',
                'leverage': trade.leverage_multiplier
            }
        )

    async def _handle_closehalf(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle CLOSEHALF command with FIFO (50%)"""
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for CLOSEHALF"
            )

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, parsed.price, 50.0
        )

        if not close_result:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Failed to execute close half"
            )

        tree_lines = self.fifo_mgr.format_fifo_tree(
            entries=close_result['trade'].entries,
            close_details=close_result['close_details'],
            symbol=trade.symbol,
            header=f"½ CLOSE HALF (50%) | {trade.symbol}",
            booked_pnl=close_result['booked_pnl'],
            remaining_size=close_result['remaining_size'],
            weighted_avg=close_result['new_weighted_avg'],
            current_stop=trade.current_stop or 0,
            leverage=trade.leverage_multiplier,
            platform='telegram'
        )

        return ExecutionResult(
            success=True,
            trade=close_result['trade'],
            message_type='close_half_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': 50,
                'price': parsed.price,
                'tree_lines': tree_lines,
                'booked_pnl': f"{close_result['booked_pnl']:+.2f}",
                'remaining_size': close_result['remaining_size'],
                'weighted_avg': close_result['new_weighted_avg'],
                'current_stop': trade.current_stop or 0,
                'status': 'OPEN',
                'leverage': trade.leverage_multiplier
            }
        )

    async def _handle_target(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle TARGET command"""
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for TARGET"
            )

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, parsed.price, 100.0
        )

        updated = self.trade_service.update_trade_status(
            trade.trade_id, TradeStatus.CLOSED
        )

        entry_price = trade.weighted_avg_entry
        price_change = parsed.price - entry_price
        position_return = (price_change / entry_price * 100) if entry_price else 0

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='target_hit_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': '100',
                'price': parsed.price,
                'entry': entry_price,
                'price_change': f"{position_return:+.2f}%",
                'position_return': f"{position_return * trade.leverage_multiplier:+.2f}%",
                'status': 'TARGET MET',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_stopped(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle STOPPED command"""
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for STOPPED"
            )

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, parsed.price, 100.0
        )

        updated = self.trade_service.update_trade_status(
            trade.trade_id, TradeStatus.CLOSED
        )

        entry_price = trade.weighted_avg_entry
        price_change = parsed.price - entry_price
        position_return = (price_change / entry_price * 100) if entry_price else 0

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='stopped_out_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': '100',
                'price': parsed.price,
                'entry': entry_price,
                'price_change': f"{position_return:+.2f}%",
                'position_return': f"{position_return * trade.leverage_multiplier:+.2f}%",
                'status': 'STOPPED',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_breakeven(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle BREAKEVEN command"""
        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, trade.weighted_avg_entry, 100.0
        )

        updated = self.trade_service.update_trade_status(
            trade.trade_id, TradeStatus.CLOSED
        )

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='breakeven_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': '100',
                'price': trade.weighted_avg_entry,
                'entry': trade.weighted_avg_entry,
                'price_change': '0.00%',
                'position_return': '0.00%',
                'status': 'BREAKEVEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_update_stop(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle UPDATE_STOP command"""
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for UPDATE_STOP"
            )

        updated = self.trade_service.update_trade_status(
            trade.trade_id,
            TradeStatus.OPEN,
            current_stop=parsed.price
        )

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='stop_update_specific',
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_update_target(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle UPDATE_TARGET command"""
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for UPDATE_TARGET"
            )

        updated_trade = trade
        updated_trade.target = parsed.price
        self.trade_service.repo.save(updated_trade)

        return ExecutionResult(
            success=True,
            trade=updated_trade,
            message_type='target_update_specific',
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_note(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle NOTE command"""
        note_text = parsed.note_text or "No note provided"

        return ExecutionResult(
            success=True,
            trade=trade,
            message_type='note_update_specific',
            variables={
                'symbol': trade.symbol,
                'note_text': note_text,
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_cancelled(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle CANCELLED command"""
        reason = parsed.reason or "Price never reached entry zone or no longer valid"

        updated = self.trade_service.update_trade_status(
            trade.trade_id, TradeStatus.CANCELLED
        )

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='trade_cancelled_specific',
            variables={
                'symbol': trade.symbol,
                'status': 'CANCELLED',
                'reason': reason,
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_not_triggered(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle NOT_TRIGGERED command"""
        reason = parsed.reason or "Price never reached entry zone or no longer valid"

        updated = self.trade_service.update_trade_status(
            trade.trade_id, TradeStatus.NOT_TRIGGERED
        )

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='trade_cancelled_specific',
            variables={
                'symbol': trade.symbol,
                'status': 'NOT TRIGGERED',
                'reason': reason,
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_pyramid(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        """Handle PYRAMID command"""
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for PYRAMID"
            )

        size_percentage = parsed.size_percentage or 50.0
        size = size_percentage / 100.0

        updated = self.trade_service.add_pyramid_entry(
            trade.trade_id, parsed.price, size
        )

        if not updated:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Failed to add pyramid entry"
            )

        return ExecutionResult(
            success=True,
            trade=updated,
            message_type='pyramid_update_specific',
            variables={
                'symbol': trade.symbol,
                'entries_count': len(updated.entries),
                'weighted_avg_entry': updated.weighted_avg_entry,
                'total_size': sum(e.size for e in updated.entries),
                'current_stop': updated.current_stop or 0,
                'status': 'OPEN',
                'leverage_multiplier': updated.leverage_multiplier
            }
        )

    def list_handlers(self) -> List[str]:
        """List registered handlers"""
        return list(self.handlers.keys())

# Singleton
_executor: Optional[ConfigExecutor] = None

def get_executor() -> ConfigExecutor:
    global _executor
    if _executor is None:
        _executor = ConfigExecutor()
    return _executor

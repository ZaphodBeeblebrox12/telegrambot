"""Config-driven Command Executor - Dynamic handler registration"""
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass

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
        if not self.cfg.command_processing:
            return

        update_config = self.cfg.command_processing.get('/update')
        if update_config and update_config.get('command_mapping'):
            for cmd, mapping in update_config['command_mapping'].items():
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

    def _format_price(self, price: float) -> str:
        """Format price for display"""
        if price >= 1000:
            return f"{price:.2f}"
        elif price >= 1:
            return f"{price:.4f}"
        else:
            return f"{price:.6f}"

    async def _handle_trail(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
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
                'price': self._format_price(parsed.price),
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_closed(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for CLOSED"
            )

        exit_price = float(parsed.price)

        # Use consolidated calculation (FIX 4)
        entry_price = self.trade_service.calculate_weighted_avg(trade)
        price_change_pct = self.trade_service.calculate_percentage_change(entry_price, exit_price)
        position_return_pct = self.trade_service.calculate_position_return(entry_price, exit_price, trade.leverage_multiplier)

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, exit_price, 100.0
        )

        updated = self.trade_service.update_trade_status(
            trade.trade_id, TradeStatus.CLOSED
        )

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='trade_close_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': '100',
                'price': self._format_price(exit_price),
                'entry': self._format_price(entry_price),
                'price_change': f"{price_change_pct:+.2f}%",
                'position_return': f"{position_return_pct:+.2f}%",
                'status': 'CLOSED',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_partial(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for PARTIAL"
            )

        exit_price = float(parsed.price)
        percentage = float(parsed.percentage) if parsed.percentage else 25.0

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, exit_price, percentage
        )

        if not close_result:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Failed to execute partial close"
            )

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
                'price': self._format_price(exit_price),
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
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for CLOSEHALF"
            )

        exit_price = float(parsed.price)

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, exit_price, 50.0
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
                'price': self._format_price(exit_price),
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
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for TARGET"
            )

        exit_price = float(parsed.price)

        entry_price = self.trade_service.calculate_weighted_avg(trade)
        price_change_pct = self.trade_service.calculate_percentage_change(entry_price, exit_price)
        position_return_pct = self.trade_service.calculate_position_return(entry_price, exit_price, trade.leverage_multiplier)

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, exit_price, 100.0
        )

        updated = self.trade_service.update_trade_status(
            trade.trade_id, TradeStatus.CLOSED
        )

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='target_hit_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': '100',
                'price': self._format_price(exit_price),
                'entry': self._format_price(entry_price),
                'price_change': f"{price_change_pct:+.2f}%",
                'position_return': f"{position_return_pct:+.2f}%",
                'status': 'TARGET MET',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_stopped(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for STOPPED"
            )

        exit_price = float(parsed.price)

        entry_price = self.trade_service.calculate_weighted_avg(trade)
        price_change_pct = self.trade_service.calculate_percentage_change(entry_price, exit_price)
        position_return_pct = self.trade_service.calculate_position_return(entry_price, exit_price, trade.leverage_multiplier)

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, exit_price, 100.0
        )

        updated = self.trade_service.update_trade_status(
            trade.trade_id, TradeStatus.CLOSED
        )

        return ExecutionResult(
            success=updated is not None,
            trade=updated or trade,
            message_type='stopped_out_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': '100',
                'price': self._format_price(exit_price),
                'entry': self._format_price(entry_price),
                'price_change': f"{price_change_pct:+.2f}%",
                'position_return': f"{position_return_pct:+.2f}%",
                'status': 'STOPPED',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_breakeven(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
        entry_price = self.trade_service.calculate_weighted_avg(trade)

        close_result = self.trade_service.execute_partial_close(
            trade.trade_id, entry_price, 100.0
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
                'price': self._format_price(entry_price),
                'entry': self._format_price(entry_price),
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
                'price': self._format_price(parsed.price),
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_update_target(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
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
                'price': self._format_price(parsed.price),
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_note(
        self,
        trade: Trade,
        parsed: ParsedCommand
    ) -> ExecutionResult:
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
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for PYRAMID"
            )

        size_percentage = float(parsed.size_percentage) if parsed.size_percentage else 50.0
        size = size_percentage / 100.0

        updated = self.trade_service.add_pyramid_entry(
            trade.trade_id, parsed.price, size
        )

        if not updated:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Failed to add pyramid entry"
            )

        # Use consolidated calculation
        weighted_avg = self.trade_service.calculate_weighted_avg(updated)
        total_size = self.trade_service.calculate_total_remaining(updated)

        return ExecutionResult(
            success=True,
            trade=updated,
            message_type='pyramid_update_specific',
            variables={
                'symbol': trade.symbol,
                'entries_count': len(updated.entries),
                'weighted_avg_entry': weighted_avg,
                'total_size': total_size,
                'current_stop': updated.current_stop or 0,
                'status': 'OPEN',
                'leverage_multiplier': updated.leverage_multiplier
            }
        )

    def list_handlers(self) -> List[str]:
        """List registered handlers"""
        return list(self.handlers.keys())

_executor = None

def get_executor():
    global _executor
    if _executor is None:
        _executor = ConfigExecutor()
    return _executor

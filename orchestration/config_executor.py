"""Config-driven Execution Engine - Dynamic handler resolution via reflection"""
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
import re

from config.config_loader import config
from core.models import Trade, TradeEntry, TradeUpdate, EntryType, TradeStatus, ParsedCommand
from core.repositories import RepositoryFactory
from core.fifo import get_fifo_manager
from core.id_generator import get_id_generator


@dataclass
class ExecutionResult:
    """Result of command execution"""
    success: bool
    message_type: Optional[str]
    variables: Dict[str, Any]
    trade: Optional[Trade]
    error: Optional[str] = None


class TradeExecutionService:
    """
    Service that handles all trade operations.
    Methods are called DYNAMICALLY via getattr based on config.
    """

    def __init__(self):
        self.trade_repo = RepositoryFactory.get_trade_repository()
        self.fifo_mgr = get_fifo_manager()
        self.id_gen = get_id_generator()

    # ============ DYNAMIC HANDLER METHODS ============
    # These are called via getattr() based on config command_mapping

    async def handle_trail(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle TRAIL updates"""
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for trail")

        trade.current_stop = parsed.price

        # Calculate locked profit from config
        if config.trade_ledger.locked_profit_calculation.get('enabled'):
            if trade.side == 'LONG':
                locked = parsed.price - trade.entry_price
            else:
                locked = trade.entry_price - parsed.price
            trade.locked_profit = max(0, locked)

        trade.updates.append(TradeUpdate(
            update_type='TRAIL',
            timestamp=datetime.now().timestamp(),
            price=parsed.price
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='trail_update_specific',
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'status': trade.status.value,
                'leverage_multiplier': trade.leverage_multiplier
            },
            trade=trade
        )

    async def handle_closed(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle CLOSE updates"""
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for close")

        trade.status = TradeStatus.CLOSED
        trade.updates.append(TradeUpdate(
            update_type='CLOSED',
            timestamp=datetime.now().timestamp(),
            price=parsed.price
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='trade_close_specific',
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'percentage': '100',
                'status': 'CLOSED',
                'leverage_multiplier': trade.leverage_multiplier
            },
            trade=trade
        )

    async def handle_target(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle TARGET hit updates"""
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for target")

        trade.status = TradeStatus.CLOSED
        trade.updates.append(TradeUpdate(
            update_type='TARGET',
            timestamp=datetime.now().timestamp(),
            price=parsed.price
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='target_hit_specific',
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'percentage': '100',
                'status': 'TARGET MET',
                'leverage_multiplier': trade.leverage_multiplier
            },
            trade=trade
        )

    async def handle_stopped(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle STOPPED OUT updates"""
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for stopped")

        trade.status = TradeStatus.CLOSED
        trade.updates.append(TradeUpdate(
            update_type='STOPPED',
            timestamp=datetime.now().timestamp(),
            price=parsed.price
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='stopped_out_specific',
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'percentage': '100',
                'status': 'STOPPED',
                'leverage_multiplier': trade.leverage_multiplier
            },
            trade=trade
        )

    async def handle_breakeven(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle BREAKEVEN updates"""
        trade.status = TradeStatus.CLOSED
        trade.updates.append(TradeUpdate(
            update_type='BREAKEVEN',
            timestamp=datetime.now().timestamp()
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='breakeven_specific',
            variables={
                'symbol': trade.symbol,
                'price': trade.entry_price,
                'percentage': '100',
                'status': 'BREAKEVEN',
                'leverage_multiplier': trade.leverage_multiplier
            },
            trade=trade
        )

    async def handle_partial(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle PARTIAL CLOSE (FIFO-based)"""
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for partial close")

        percentage = parsed.percentage or 25.0

        # Calculate FIFO close
        close_details, booked_pnl, remaining_size, new_weighted_avg = \
            self.fifo_mgr.calculate_fifo_close(
                entries=trade.entries,
                exit_price=parsed.price,
                close_percentage=percentage,
                side=trade.side
            )

        self.fifo_mgr.apply_close(trade.entries, close_details)

        close_record = self.fifo_mgr.create_close_record(
            close_percentage=percentage,
            exit_price=parsed.price,
            close_details=close_details,
            booked_pnl=booked_pnl,
            remaining_size=remaining_size,
            new_weighted_avg=new_weighted_avg
        )
        trade.add_fifo_close(close_record)

        trade.updates.append(TradeUpdate(
            update_type='PARTIAL',
            timestamp=datetime.now().timestamp(),
            price=parsed.price,
            percentage=percentage
        ))

        self.trade_repo.save(trade)

        msg_type = 'partial_close_specific' if percentage <= 25 else 'close_half_specific'
        if trade.entries_count > 1:
            msg_type = 'partial_close_pyramid_specific' if percentage <= 25 else 'close_half_pyramid_specific'

        return ExecutionResult(
            success=True,
            message_type=msg_type,
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'percentage': percentage,
                'remaining': 100 - percentage,
                'booked_pnl': booked_pnl,
                'status': trade.status.value,
                'leverage_multiplier': trade.leverage_multiplier,
                'entry_count': trade.entries_count
            },
            trade=trade
        )

    async def handle_closehalf(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle CLOSE HALF (50%)"""
        parsed.percentage = 50.0
        return await self.handle_partial(trade, parsed, **kwargs)

    async def handle_update_stop(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle STOP UPDATE"""
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for stop update")

        trade.current_stop = parsed.price
        trade.updates.append(TradeUpdate(
            update_type='UPDATE_STOP',
            timestamp=datetime.now().timestamp(),
            price=parsed.price
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='stop_update_specific',
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'status': trade.status.value,
                'leverage_multiplier': trade.leverage_multiplier
            },
            trade=trade
        )

    async def handle_newtarget(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle TARGET UPDATE"""
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for target update")

        trade.target = parsed.price
        trade.updates.append(TradeUpdate(
            update_type='UPDATE_TARGET',
            timestamp=datetime.now().timestamp(),
            price=parsed.price
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='target_update_specific',
            variables={
                'symbol': trade.symbol,
                'price': parsed.price,
                'status': trade.status.value,
                'leverage_multiplier': trade.leverage_multiplier
            },
            trade=trade
        )

    async def handle_note(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle NOTE updates"""
        note_text = parsed.note_text or kwargs.get('note_text', 'No note provided')

        trade.updates.append(TradeUpdate(
            update_type='NOTE',
            timestamp=datetime.now().timestamp(),
            note_text=note_text
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='note_update_specific',
            variables={
                'symbol': trade.symbol,
                'note_text': note_text,
                'status': trade.status.value,
                'leverage_multiplier': trade.leverage_multiplier
            },
            trade=trade
        )

    async def handle_cancelled(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle CANCELLED updates"""
        trade.status = TradeStatus.CANCELLED
        reason = parsed.reason or kwargs.get('reason', 'Price never reached entry zone')

        trade.updates.append(TradeUpdate(
            update_type='CANCELLED',
            timestamp=datetime.now().timestamp(),
            note_text=reason
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='trade_cancelled_specific',
            variables={
                'symbol': trade.symbol,
                'status': 'CANCELLED',
                'reason': reason,
                'leverage_multiplier': trade.leverage_multiplier
            },
            trade=trade
        )

    async def handle_pyramid(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        """Handle PYRAMID add to position"""
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for pyramid")

        size_pct = parsed.size_percentage or config.pyramid_settings.get('default_size_percentage', 50)

        # Generate deterministic entry ID
        entry_index = len(trade.entries) + 1
        entry_id = self.id_gen.generate_entry_id(trade.trade_id, 'PYRAMID', entry_index)

        entry = TradeEntry(
            entry_id=entry_id,
            entry_price=parsed.price,
            size=size_pct / 100,
            type=EntryType.PYRAMID,
            timestamp=datetime.now().timestamp()
        )

        trade.add_entry(entry)
        trade.updates.append(TradeUpdate(
            update_type='PYRAMID',
            timestamp=datetime.now().timestamp(),
            price=parsed.price,
            data={'size_percentage': size_pct}
        ))

        self.trade_repo.save(trade)

        return ExecutionResult(
            success=True,
            message_type='pyramid_update_specific',
            variables={
                'symbol': trade.symbol,
                'pyramid_entry': parsed.price,
                'pyramid_size': size_pct,
                'weighted_avg_entry': trade.weighted_avg_entry,
                'total_size': trade.total_position_size * 100,
                'current_stop': trade.current_stop or trade.stop_loss,
                'breakeven_stop': trade.entry_price,
                'status': trade.status.value,
                'leverage_multiplier': trade.leverage_multiplier,
                'entries_count': trade.entries_count
            },
            trade=trade
        )


class ConfigExecutor:
    """
    Config-driven execution engine using REFLECTION (getattr).

    NO hardcoded handler mapping - methods resolved dynamically from config.
    """

    def __init__(self):
        self.service = TradeExecutionService()
        self._build_execution_map()

    def _build_execution_map(self):
        """Build execution map from config.command_processing"""
        update_config = config.commands.get('/update')
        if not update_config or not update_config.command_mapping:
            return

        # Store config mapping for reference
        self.command_mapping = update_config.command_mapping

    def _get_handler_method(self, update_type: str) -> Optional[Callable]:
        """
        Get handler method DYNAMICALLY via getattr.

        Converts update_type like 'TRAIL' -> 'handle_trail'
        'UPDATE_STOP' -> 'handle_update_stop'
        """
        # Normalize update type
        normalized = update_type.lower().replace('_', '')
        method_name = f"handle_{normalized}"

        # Try to get method from service using reflection
        method = getattr(self.service, method_name, None)
        if method and callable(method):
            return method

        # Fallback: try direct match
        method_name = f"handle_{update_type.lower()}"
        method = getattr(self.service, method_name, None)
        if method and callable(method):
            return method

        return None

    async def execute(
        self,
        trade: Trade,
        parsed: ParsedCommand,
        **kwargs
    ) -> ExecutionResult:
        """Execute command against trade using DYNAMIC method resolution"""
        update_type = parsed.update_type or parsed.subcommand

        if not update_type:
            return ExecutionResult(False, None, {}, trade, "No update type specified")

        # Get handler method via REFLECTION (getattr)
        handler = self._get_handler_method(update_type)

        if not handler:
            return ExecutionResult(
                False, None, {}, trade, 
                f"Unknown update type: {update_type} (no handler method found)"
            )

        # Execute handler
        try:
            return await handler(trade, parsed, **kwargs)
        except Exception as e:
            return ExecutionResult(False, None, {}, trade, str(e))

    def list_handlers(self) -> list:
        """List all available handlers via introspection"""
        handlers = []
        for attr_name in dir(self.service):
            if attr_name.startswith('handle_') and callable(getattr(self.service, attr_name)):
                handlers.append(attr_name.replace('handle_', '').upper())
        return handlers


# Singleton
_executor: Optional[ConfigExecutor] = None

def get_executor() -> ConfigExecutor:
    global _executor
    if _executor is None:
        _executor = ConfigExecutor()
    return _executor

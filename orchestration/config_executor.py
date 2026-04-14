"""Config-driven Execution Engine - Dynamic handler resolution"""
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
import uuid

from config.config_loader import config
from core.models import Trade, TradeEntry, TradeUpdate, EntryType, TradeStatus, ParsedCommand
from core.repositories import RepositoryFactory
from core.fifo import get_fifo_manager
from orchestration.formatter import get_formatter


@dataclass
class ExecutionResult:
    """Result of command execution"""
    success: bool
    message_type: Optional[str]
    variables: Dict[str, Any]
    trade: Optional[Trade]
    error: Optional[str] = None


class ExecutionHandler:
    """Base class for execution handlers"""

    def __init__(self):
        self.trade_repo = RepositoryFactory.get_trade_repository()
        self.fifo_mgr = get_fifo_manager()
        self.formatter = get_formatter()

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        raise NotImplementedError


class TrailUpdateHandler(ExecutionHandler):
    """Handle TRAIL updates"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for trail")

        trade.current_stop = parsed.price

        # Calculate locked profit
        if config.trade_ledger.locked_profit_calculation.get('enabled'):
            if trade.side == 'LONG':
                locked = parsed.price - trade.entry_price
            else:
                locked = trade.entry_price - parsed.price
            trade.locked_profit = max(0, locked)

        # Add update record
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


class CloseHandler(ExecutionHandler):
    """Handle CLOSE updates"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
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


class TargetHitHandler(ExecutionHandler):
    """Handle TARGET hit updates"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
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


class StoppedOutHandler(ExecutionHandler):
    """Handle STOPPED OUT updates"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
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


class BreakevenHandler(ExecutionHandler):
    """Handle BREAKEVEN updates"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
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


class PartialCloseHandler(ExecutionHandler):
    """Handle PARTIAL CLOSE (FIFO-based)"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for partial close")

        percentage = parsed.percentage or 25.0

        # Calculate FIFO close
        close_details, booked_pnl, remaining_size, new_weighted_avg =             self.fifo_mgr.calculate_fifo_close(
                entries=trade.entries,
                exit_price=parsed.price,
                close_percentage=percentage,
                side=trade.side
            )

        # Apply close to entries
        self.fifo_mgr.apply_close(trade.entries, close_details)

        # Create close record
        close_record = self.fifo_mgr.create_close_record(
            close_percentage=percentage,
            exit_price=parsed.price,
            close_details=close_details,
            booked_pnl=booked_pnl,
            remaining_size=remaining_size,
            new_weighted_avg=new_weighted_avg
        )
        trade.add_fifo_close(close_record)

        # Add update
        trade.updates.append(TradeUpdate(
            update_type='PARTIAL',
            timestamp=datetime.now().timestamp(),
            price=parsed.price,
            percentage=percentage
        ))

        self.trade_repo.save(trade)

        # Determine message type
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


class CloseHalfHandler(ExecutionHandler):
    """Handle CLOSE HALF (50%)"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        parsed.percentage = 50.0
        return PartialCloseHandler().execute(trade, parsed, **kwargs)


class StopUpdateHandler(ExecutionHandler):
    """Handle STOP UPDATE"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
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


class TargetUpdateHandler(ExecutionHandler):
    """Handle TARGET UPDATE"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
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


class NoteHandler(ExecutionHandler):
    """Handle NOTE updates"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
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


class CancelledHandler(ExecutionHandler):
    """Handle CANCELLED updates"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
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


class PyramidHandler(ExecutionHandler):
    """Handle PYRAMID add to position"""

    def execute(self, trade: Trade, parsed: ParsedCommand, **kwargs) -> ExecutionResult:
        if not parsed.price:
            return ExecutionResult(False, None, {}, trade, "Price required for pyramid")

        size_pct = parsed.size_percentage or config.pyramid_settings.get('default_size_percentage', 50)

        entry = TradeEntry(
            entry_id=str(uuid.uuid4()),
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
    """Config-driven execution engine - maps commands to handlers dynamically"""

    def __init__(self):
        self.handlers: Dict[str, ExecutionHandler] = {}
        self._register_handlers()

    def _register_handlers(self):
        """Register all handlers from config"""
        # Map update types to handlers
        self.handlers = {
            'TRAIL': TrailUpdateHandler(),
            'CLOSED': CloseHandler(),
            'TARGET': TargetHitHandler(),
            'STOPPED': StoppedOutHandler(),
            'BREAKEVEN': BreakevenHandler(),
            'BE': BreakevenHandler(),
            'PARTIAL': PartialCloseHandler(),
            'CLOSEHALF': CloseHalfHandler(),
            'HALF': CloseHalfHandler(),
            'UPDATE_STOP': StopUpdateHandler(),
            'STOP': StopUpdateHandler(),
            'NEWTARGET': TargetUpdateHandler(),
            'UPDATE_TARGET': TargetUpdateHandler(),
            'NOTE': NoteHandler(),
            'CANCELLED': CancelledHandler(),
            'CANCEL': CancelledHandler(),
            'NOT_TRIGGERED': CancelledHandler(),
            'PYRAMID': PyramidHandler(),
        }

    def execute(
        self,
        trade: Trade,
        parsed: ParsedCommand,
        **kwargs
    ) -> ExecutionResult:
        """Execute command against trade"""
        update_type = parsed.update_type or parsed.subcommand

        if not update_type:
            return ExecutionResult(False, None, {}, trade, "No update type specified")

        handler = self.handlers.get(update_type.upper())
        if not handler:
            return ExecutionResult(False, None, {}, trade, f"Unknown update type: {update_type}")

        return handler.execute(trade, parsed, **kwargs)

    def get_handler(self, update_type: str) -> Optional[ExecutionHandler]:
        """Get handler for update type"""
        return self.handlers.get(update_type.upper())

    def list_handlers(self) -> list:
        """List all registered handlers"""
        return list(self.handlers.keys())


# Singleton
_executor: Optional[ConfigExecutor] = None

def get_executor() -> ConfigExecutor:
    global _executor
    if _executor is None:
        _executor = ConfigExecutor()
    return _executor

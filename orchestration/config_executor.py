"""
Config-driven Command Executor - PRODUCTION VERSION (FIXED)
Drop-in replacement with:
- Transaction safety (NO commit inside, caller manages it)
- Closed trade protection
- Guaranteed snapshot rebuild
- Audit logging with before/after state
"""
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
import hashlib
import json
import logging

from config.config_loader import config
from core.models import Trade, ParsedCommand, TradeStatus, EntryType
from core.services import get_trade_service
from core.fifo import get_fifo_manager
from core.id_generator import get_id_generator
from core.db import Database, TradeModel, TradeEntryModel, TradeSnapshotModel, MessageMappingModel, TradeEventModel
from sqlalchemy import select, update

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of command execution (UNCHANGED INTERFACE)"""
    success: bool
    trade: Optional[Trade]
    message_type: Optional[str]
    variables: Dict[str, Any]
    error: Optional[str] = None


class ConfigExecutor:
    """
    PRODUCTION command executor with full safety guarantees.

    CRITICAL: This class does NOT commit the session.
    Caller must commit after calling execute().
    """

    def __init__(self):
        self.cfg = config
        self.trade_service = get_trade_service()
        self.fifo_mgr = get_fifo_manager()
        self.id_gen = get_id_generator()
        self.db = Database()
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

    def _generate_idempotency_key(self, trade_id: str, command: str, payload: Dict) -> str:
        normalized = json.dumps(payload, sort_keys=True, default=str)
        payload_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        return f"{trade_id}:{command}:{payload_hash}"

    def _check_idempotency(self, session, idempotency_key: str) -> bool:
        existing = session.execute(
            select(TradeEventModel).where(TradeEventModel.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        return existing is not None

    def _check_trade_not_closed(self, session, trade_db_id: int) -> tuple:
        """Check if trade is closed. Returns (is_ok, error_message)"""
        trade_status = session.execute(
            select(TradeModel.status).where(TradeModel.id == trade_db_id)
        ).scalar_one_or_none()

        if trade_status is None:
            return False, "Trade not found"

        if trade_status in ("CLOSED", "CANCELLED", "NOT_TRIGGERED"):
            return False, f"Trade already {trade_status.lower()} - updates not allowed"

        return True, None

    def _capture_state(self, session, trade_db_id: int) -> Dict[str, Any]:
        """Capture current state for audit"""
        trade = session.execute(
            select(TradeModel).where(TradeModel.id == trade_db_id)
        ).scalar_one_or_none()

        if not trade:
            return {}

        snapshot = session.execute(
            select(TradeSnapshotModel).where(TradeSnapshotModel.trade_id == trade_db_id)
        ).scalar_one_or_none()

        entries = session.execute(
            select(TradeEntryModel).where(TradeEntryModel.trade_id == trade_db_id)
        ).scalars().all()

        return {
            "status": trade.status,
            "entries": [
                {"seq": e.sequence, "price": float(e.entry_price),
                 "size": float(e.size), "closed": float(e.closed_size)}
                for e in entries
            ],
            "snapshot": {
                "avg": float(snapshot.weighted_avg_entry) if snapshot else None,
                "remaining": float(snapshot.remaining_size) if snapshot else None
            }
        }

    def _rebuild_snapshot(self, session, trade_db_id: int):
        """Guaranteed snapshot rebuild"""
        from decimal import Decimal

        entries = session.execute(
            select(TradeEntryModel).where(TradeEntryModel.trade_id == trade_db_id)
        ).scalars().all()

        total_size = Decimal('0')
        remaining_size = Decimal('0')
        weighted_sum = Decimal('0')

        for entry in entries:
            size = Decimal(str(entry.size))
            closed = Decimal(str(entry.closed_size))
            remaining = size - closed

            total_size += size
            remaining_size += remaining
            if remaining > 0:
                weighted_sum += Decimal(str(entry.entry_price)) * remaining

        weighted_avg = weighted_sum / remaining_size if remaining_size > 0 else Decimal('0')

        # Try update first
        result = session.execute(
            update(TradeSnapshotModel)
            .where(TradeSnapshotModel.trade_id == trade_db_id)
            .values(
                weighted_avg_entry=float(weighted_avg),
                total_size=float(total_size),
                remaining_size=float(remaining_size)
            )
        )

        # If no rows updated, insert new
        if result.rowcount == 0:
            snapshot = TradeSnapshotModel(
                trade_id=trade_db_id,
                weighted_avg_entry=float(weighted_avg),
                total_size=float(total_size),
                remaining_size=float(remaining_size),
                locked_profit=0.0,
                total_booked_pnl=0.0
            )
            session.add(snapshot)

    def _record_event(self, session, trade_db_id: int, event_type: str,
                      payload: Dict, idempotency_key: str,
                      before_state: Dict, after_state: Dict):
        """Record event with audit trail"""
        audit_payload = {
            "payload": payload,
            "before": before_state,
            "after": after_state,
            "timestamp": datetime.utcnow().isoformat()
        }

        event = TradeEventModel(
            trade_id=trade_db_id,
            event_type=event_type,
            payload=json.dumps(audit_payload, default=str),
            idempotency_key=idempotency_key
        )
        session.add(event)

    async def execute(self, trade: Trade, parsed: ParsedCommand) -> ExecutionResult:
        """Execute parsed command with full transaction safety.

        CRITICAL: Does NOT commit session. Caller must commit.
        """
        handler = self.handlers.get(parsed.subcommand)

        if not handler:
            return ExecutionResult(
                success=False,
                trade=trade,
                message_type=None,
                variables={},
                error=f"No handler for command: {parsed.subcommand}"
            )

        session = self.db.get_session()
        try:
            # Get trade DB ID
            result = session.execute(
                select(TradeModel.id, TradeModel.side, TradeModel.stop_loss, TradeModel.status)
                .where(TradeModel.trade_id == trade.trade_id)
            ).first()

            if not result:
                return ExecutionResult(
                    success=False,
                    trade=trade,
                    message_type=None,
                    variables={},
                    error=f"Trade not found in DB: {trade.trade_id}"
                )

            trade_db_id, side, stop_loss, status = result

            # CLOSED TRADE PROTECTION
            if status in ("CLOSED", "CANCELLED", "NOT_TRIGGERED"):
                return ExecutionResult(
                    success=False,
                    trade=trade,
                    message_type=None,
                    variables={},
                    error=f"Trade already {status.lower()} - updates not allowed"
                )

            # Build payload
            payload = self._build_payload(parsed)

            # Idempotency check
            idempotency_key = self._generate_idempotency_key(
                trade.trade_id, parsed.subcommand, payload
            )

            if self._check_idempotency(session, idempotency_key):
                logger.info(f"Idempotent skip: {idempotency_key}")
                return ExecutionResult(
                    success=True,
                    trade=trade,
                    message_type=None,
                    variables={}
                )

            # Capture before state
            before_state = self._capture_state(session, trade_db_id)

            # Execute handler
            result = await handler(
                session=session,
                trade=trade,
                trade_db_id=trade_db_id,
                parsed=parsed,
                side=side,
                stop_loss=stop_loss
            )

            # GUARANTEED SNAPSHOT REBUILD (before commit)
            if result.success:
                self._rebuild_snapshot(session, trade_db_id)

            # Capture after state
            after_state = self._capture_state(session, trade_db_id)

            # Record event with audit
            self._record_event(
                session, trade_db_id, parsed.subcommand,
                payload, idempotency_key, before_state, after_state
            )

            # CRITICAL FIX: DO NOT commit here - caller manages transaction
            return result

        except Exception as e:
            session.rollback()
            logger.error(f"Execution failed: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                trade=trade,
                message_type=None,
                variables={},
                error=str(e)
            )
        finally:
            # CRITICAL FIX: DO NOT close session here - caller manages it
            pass

    def _build_payload(self, parsed: ParsedCommand) -> Dict:
        payload = {}
        if parsed.price is not None:
            payload['price'] = parsed.price
        if parsed.percentage is not None:
            payload['percentage'] = parsed.percentage
        if parsed.size_percentage is not None:
            payload['size_percentage'] = parsed.size_percentage
        if parsed.note_text is not None:
            payload['note_text'] = parsed.note_text
        if parsed.reason is not None:
            payload['reason'] = parsed.reason
        return payload

    def _format_price(self, price: float) -> str:
        if price >= 1000:
            return f"{price:.2f}"
        elif price >= 1:
            return f"{price:.4f}"
        else:
            return f"{price:.6f}"

    # ===== HANDLERS =====

    async def _handle_trail(self, session, trade: Trade, trade_db_id: int,
                            parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for TRAIL"
            )

        session.execute(
            update(TradeSnapshotModel)
            .where(TradeSnapshotModel.trade_id == trade_db_id)
            .values(current_stop=float(parsed.price))
        )

        return ExecutionResult(
            success=True,
            trade=trade,
            message_type='trail_update_specific',
            variables={
                'symbol': trade.symbol,
                'price': self._format_price(parsed.price),
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_partial(self, session, trade: Trade, trade_db_id: int,
                            parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        from core.fifo_engine import FIFOEngine

        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for PARTIAL"
            )

        percentage = float(parsed.percentage) if parsed.percentage else 25.0
        if parsed.subcommand == 'CLOSEHALF':
            percentage = 50.0

        fifo = FIFOEngine(session)
        calc = fifo.calculate_fifo_close(
            trade_id=trade_db_id,
            exit_price=Decimal(str(parsed.price)),
            close_percentage=Decimal(str(percentage)),
            side=side
        )

        if not calc.close_details:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="No position to close"
            )

        fifo.apply_close_to_entries(trade_db_id, calc.close_details)

        snapshot = session.execute(
            select(TradeSnapshotModel).where(TradeSnapshotModel.trade_id == trade_db_id)
        ).scalar_one_or_none()

        tree_lines = ["🔹 PARTIAL CLOSE"] + calc.tree_lines
        if parsed.subcommand == 'CLOSEHALF':
            tree_lines[0] = "½ CLOSE HALF"

        return ExecutionResult(
            success=True,
            trade=trade,
            message_type='partial_close_specific' if parsed.subcommand == 'PARTIAL' else 'close_half_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': percentage,
                'price': self._format_price(parsed.price),
                'tree_lines': "\n".join(tree_lines),
                'booked_pnl': f"{float(calc.total_pnl):+.2f}",
                'remaining_size': float(calc.remaining_size),
                'weighted_avg': float(calc.new_weighted_avg),
                'current_stop': float(snapshot.current_stop) if snapshot and snapshot.current_stop else 0,
                'status': 'OPEN',
                'leverage': trade.leverage_multiplier
            }
        )

    async def _handle_closehalf(self, session, trade: Trade, trade_db_id: int,
                                parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        parsed.percentage = 50.0
        return await self._handle_partial(session, trade, trade_db_id, parsed, side, stop_loss)

    async def _handle_closed(self, session, trade: Trade, trade_db_id: int,
                             parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        from core.fifo_engine import FIFOEngine

        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for close"
            )

        fifo = FIFOEngine(session)
        calc = fifo.calculate_fifo_close(
            trade_id=trade_db_id,
            exit_price=Decimal(str(parsed.price)),
            close_percentage=Decimal('100'),
            side=side
        )

        fifo.apply_close_to_entries(trade_db_id, calc.close_details)

        session.execute(
            update(TradeModel).where(TradeModel.id == trade_db_id)
            .values(status='CLOSED')
        )

        snapshot = session.execute(
            select(TradeSnapshotModel).where(TradeSnapshotModel.trade_id == trade_db_id)
        ).scalar_one_or_none()

        entry_price = float(snapshot.weighted_avg_entry) if snapshot else parsed.price

        if entry_price > 0:
            if side == 'LONG':
                price_change = ((parsed.price - entry_price) / entry_price) * 100
            else:
                price_change = ((entry_price - parsed.price) / entry_price) * 100
            position_return = price_change * trade.leverage_multiplier
        else:
            price_change = 0
            position_return = 0

        return ExecutionResult(
            success=True,
            trade=trade,
            message_type='trade_close_specific',
            variables={
                'symbol': trade.symbol,
                'percentage': '100',
                'price': self._format_price(parsed.price),
                'entry': self._format_price(entry_price),
                'price_change': f"{price_change:+.2f}%",
                'position_return': f"{position_return:+.2f}%",
                'status': 'CLOSED',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_target(self, session, trade: Trade, trade_db_id: int,
                             parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        result = await self._handle_closed(session, trade, trade_db_id, parsed, side, stop_loss)
        if result.success:
            result.message_type = 'target_hit_specific'
            result.variables['status'] = 'TARGET MET'
        return result

    async def _handle_stopped(self, session, trade: Trade, trade_db_id: int,
                              parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        result = await self._handle_closed(session, trade, trade_db_id, parsed, side, stop_loss)
        if result.success:
            result.message_type = 'stopped_out_specific'
            result.variables['status'] = 'STOPPED'
        return result

    async def _handle_breakeven(self, session, trade: Trade, trade_db_id: int,
                                parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        snapshot = session.execute(
            select(TradeSnapshotModel).where(TradeSnapshotModel.trade_id == trade_db_id)
        ).scalar_one_or_none()

        entry_price = float(snapshot.weighted_avg_entry) if snapshot else 0
        parsed.price = entry_price

        result = await self._handle_closed(session, trade, trade_db_id, parsed, side, stop_loss)
        if result.success:
            result.message_type = 'breakeven_specific'
            result.variables['status'] = 'BREAKEVEN'
            result.variables['price_change'] = '0.00%'
            result.variables['position_return'] = '0.00%'
        return result

    async def _handle_update_stop(self, session, trade: Trade, trade_db_id: int,
                                  parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        return await self._handle_trail(session, trade, trade_db_id, parsed, side, stop_loss)

    async def _handle_update_target(self, session, trade: Trade, trade_db_id: int,
                                    parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for UPDATE_TARGET"
            )

        session.execute(
            update(TradeModel).where(TradeModel.id == trade_db_id)
            .values(target=float(parsed.price))
        )

        return ExecutionResult(
            success=True,
            trade=trade,
            message_type='target_update_specific',
            variables={
                'symbol': trade.symbol,
                'price': self._format_price(parsed.price),
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_note(self, session, trade: Trade, trade_db_id: int,
                           parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        return ExecutionResult(
            success=True,
            trade=trade,
            message_type='note_update_specific',
            variables={
                'symbol': trade.symbol,
                'note_text': parsed.note_text or "No note provided",
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_cancelled(self, session, trade: Trade, trade_db_id: int,
                                parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        session.execute(
            update(TradeModel).where(TradeModel.id == trade_db_id)
            .values(status='CANCELLED')
        )

        return ExecutionResult(
            success=True,
            trade=trade,
            message_type='trade_cancelled_specific',
            variables={
                'symbol': trade.symbol,
                'status': 'CANCELLED',
                'reason': parsed.reason or "Price never reached entry zone",
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_not_triggered(self, session, trade: Trade, trade_db_id: int,
                                      parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        session.execute(
            update(TradeModel).where(TradeModel.id == trade_db_id)
            .values(status='NOT_TRIGGERED')
        )

        return ExecutionResult(
            success=True,
            trade=trade,
            message_type='trade_cancelled_specific',
            variables={
                'symbol': trade.symbol,
                'status': 'NOT TRIGGERED',
                'reason': parsed.reason or "Price never reached entry zone",
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    async def _handle_pyramid(self, session, trade: Trade, trade_db_id: int,
                              parsed: ParsedCommand, side: str, stop_loss) -> ExecutionResult:
        if not parsed.price:
            return ExecutionResult(
                success=False, trade=trade, message_type=None,
                variables={}, error="Price required for PYRAMID"
            )

        size_percentage = float(parsed.size_percentage) if parsed.size_percentage else 50.0
        size = size_percentage / 100.0

        result = session.execute(
            select(TradeEntryModel.sequence)
            .where(TradeEntryModel.trade_id == trade_db_id)
            .order_by(TradeEntryModel.sequence.desc())
        ).first()

        new_seq = (result[0] if result else 0) + 1

        entry = TradeEntryModel(
            trade_id=trade_db_id,
            entry_price=float(parsed.price),
            size=size,
            closed_size=0.0,
            entry_type='PYRAMID',
            sequence=new_seq
        )
        session.add(entry)

        return ExecutionResult(
            success=True,
            trade=trade,
            message_type='pyramid_update_specific',
            variables={
                'symbol': trade.symbol,
                'entries_count': new_seq,
                'current_stop': stop_loss or 0,
                'status': 'OPEN',
                'leverage_multiplier': trade.leverage_multiplier
            }
        )

    def list_handlers(self) -> List[str]:
        return list(self.handlers.keys())


_executor = None


def get_executor():
    global _executor
    if _executor is None:
        _executor = ConfigExecutor()
    return _executor

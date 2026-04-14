"""Config-driven Trade Service - Single source for all calculations (FIX 3)"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from decimal import Decimal

from config.config_loader import config
from core.models import Trade, TradeEntry, EntryType, TradeStatus, OCRResult
from core.repositories import RepositoryFactory
from core.fifo import get_fifo_manager
from core.id_generator import get_id_generator
from ocr.gemini_ocr import get_ocr_service

class TradeService:
    """Trade business logic - SINGLE SOURCE OF TRUTH for all calculations (FIX 3)"""

    def __init__(self):
        self.cfg = config.trade_ledger
        self.repo = RepositoryFactory.get_trade_repository()
        self.fifo_mgr = get_fifo_manager()
        self.ocr_service = get_ocr_service()
        self.id_gen = get_id_generator()

    # ===== CALCULATION METHODS (FIX 3: Centralized) =====

    def calculate_weighted_avg(self, trade: Trade) -> float:
        """Calculate weighted average entry - SINGLE SOURCE OF TRUTH"""
        if not trade.entries:
            return trade.entry_price

        total_remaining = sum(e.remaining_size for e in trade.entries)
        if total_remaining <= 0:
            return trade.entry_price

        weighted_sum = sum(e.entry_price * e.remaining_size for e in trade.entries)
        return weighted_sum / total_remaining

    def calculate_total_position_size(self, trade: Trade) -> float:
        """Calculate total remaining position size"""
        return sum(e.remaining_size for e in trade.entries)

    def calculate_pnl(self, trade: Trade, exit_price: float, size: float) -> float:
        """Calculate PnL for a given exit"""
        weighted_avg = self.calculate_weighted_avg(trade)

        if trade.side == "LONG":
            return (exit_price - weighted_avg) * size
        else:
            return (weighted_avg - exit_price) * size

    def calculate_locked_profit(self, trade: Trade) -> float:
        """Calculate locked profit based on current stop"""
        if not self.cfg.calculate_locked_profit or trade.current_stop is None:
            return 0.0

        weighted_avg = Decimal(str(self.calculate_weighted_avg(trade)))
        stop_price = Decimal(str(trade.current_stop))
        position_size = Decimal(str(self.calculate_total_position_size(trade)))

        if trade.side == 'LONG':
            locked = stop_price - weighted_avg
        else:
            locked = weighted_avg - stop_price

        return max(0, float(locked * position_size))

    # ===== TRADE OPERATIONS =====

    def create_trade_from_ocr(self, ocr_result: OCRResult) -> Optional[Trade]:
        """Create new trade from OCR result"""
        if not ocr_result.is_valid:
            return None

        if not self.cfg.enabled or not self.cfg.auto_create:
            return None

        trade_id = self.id_gen.generate(
            symbol=ocr_result.symbol,
            timestamp=datetime.now().timestamp()
        )

        leverage = self.ocr_service.get_leverage_multiplier(
            ocr_result.asset_class,
            ocr_result.symbol
        )

        trade = Trade(
            trade_id=trade_id,
            symbol=ocr_result.symbol,
            asset_class=ocr_result.asset_class,
            side=ocr_result.side.upper(),
            entry_price=float(ocr_result.entry.replace(',', '')),
            target=float(ocr_result.target.replace(',', '')),
            stop_loss=float(ocr_result.stop_loss.replace(',', '')),
            current_stop=float(ocr_result.stop_loss.replace(',', '')),
            leverage_multiplier=leverage,
            status=TradeStatus.OPEN
        )

        entry_id = self.id_gen.generate_entry_id(trade_id, 'INITIAL', 1)
        entry = TradeEntry(
            entry_id=entry_id,
            entry_price=trade.entry_price,
            size=1.0,
            type=EntryType.INITIAL,
            timestamp=datetime.now().timestamp()
        )
        trade.add_entry(entry)

        self.repo.save(trade)
        return trade

    def get_trade(self, trade_id: str) -> Optional[Trade]:
        return self.repo.get(trade_id)

    def get_trade_by_symbol(self, symbol: str, status: Optional[str] = 'OPEN') -> Optional[Trade]:
        trades = self.repo.get_by_symbol(symbol, status)
        return trades[0] if trades else None

    def get_open_trades(self) -> List[Trade]:
        return self.repo.get_open_trades()

    def update_trade_status(
        self,
        trade_id: str,
        status: TradeStatus,
        **kwargs
    ) -> Optional[Trade]:
        trade = self.repo.get(trade_id)
        if not trade:
            return None

        trade.status = status

        if 'current_stop' in kwargs:
            trade.current_stop = kwargs['current_stop']
            # Use centralized calculation
            trade.locked_profit = self.calculate_locked_profit(trade)

        self.repo.save(trade)
        return trade

    def add_pyramid_entry(
        self,
        trade_id: str,
        entry_price: float,
        size: float = 0.5
    ) -> Optional[Trade]:
        trade = self.repo.get(trade_id)
        if not trade:
            return None

        max_pyramids = config.pyramid_settings.get('max_pyramids_per_trade', 5)
        if len(trade.entries) >= max_pyramids:
            return None

        entry_index = len(trade.entries) + 1
        entry_id = self.id_gen.generate_entry_id(trade_id, 'PYRAMID', entry_index)

        entry = TradeEntry(
            entry_id=entry_id,
            entry_price=entry_price,
            size=size,
            type=EntryType.PYRAMID,
            timestamp=datetime.now().timestamp()
        )

        trade.add_entry(entry)
        self.repo.save(trade)
        return trade

    def execute_partial_close(
        self,
        trade_id: str,
        exit_price: float,
        close_percentage: float
    ) -> Optional[Dict[str, Any]]:
        """
        Execute partial close with FIFO logic.
        Uses centralized calculation methods (FIX 3).
        """
        trade = self.repo.get(trade_id)
        if not trade:
            return None

        close_details, booked_pnl, remaining_size, new_weighted_avg =             self.fifo_mgr.calculate_fifo_close(
                entries=trade.entries,
                exit_price=exit_price,
                close_percentage=close_percentage,
                side=trade.side
            )

        # Apply close (accumulates closed_size)
        self.fifo_mgr.apply_close(trade.entries, close_details)

        close_record = self.fifo_mgr.create_close_record(
            close_percentage=close_percentage,
            exit_price=exit_price,
            close_details=close_details,
            booked_pnl=booked_pnl,
            remaining_size=remaining_size,
            new_weighted_avg=new_weighted_avg
        )
        trade.add_fifo_close(close_record)

        self.repo.save(trade)

        return {
            'trade': trade,
            'close_details': close_details,
            'booked_pnl': booked_pnl,
            'remaining_size': remaining_size,
            'new_weighted_avg': new_weighted_avg
        }

    def cleanup_old_trades(self) -> int:
        cleanup_days = self.cfg.cleanup_days
        cutoff = datetime.now().timestamp() - (cleanup_days * 86400)

        all_trades = self.repo.get_all()
        deleted = 0

        for trade in all_trades:
            if trade.status != TradeStatus.OPEN and trade.created_at < cutoff:
                if self.repo.delete(trade.trade_id):
                    deleted += 1

        return deleted

    def get_trade_statistics(self) -> Dict[str, Any]:
        all_trades = self.repo.get_all()
        open_trades = [t for t in all_trades if t.status == TradeStatus.OPEN]
        closed_trades = [t for t in all_trades if t.status == TradeStatus.CLOSED]

        return {
            'total_trades': len(all_trades),
            'open_trades': len(open_trades),
            'closed_trades': len(closed_trades),
            'cancelled_trades': len([t for t in all_trades if t.status == TradeStatus.CANCELLED]),
            'avg_entries_per_trade': sum(len(t.entries) for t in all_trades) / max(len(all_trades), 1),
            'trades_with_fifo_closes': len([t for t in all_trades if t.fifo_closes])
        }

_trade_service: Optional[TradeService] = None

def get_trade_service() -> TradeService:
    global _trade_service
    if _trade_service is None:
        _trade_service = TradeService()
    return _trade_service

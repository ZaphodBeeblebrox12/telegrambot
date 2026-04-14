"""Config-driven Trade Service - Business logic layer"""
from typing import Optional, Dict, Any, List
from datetime import datetime

from config.config_loader import config
from core.models import Trade, TradeEntry, EntryType, TradeStatus, OCRResult
from core.repositories import RepositoryFactory
from core.fifo import get_fifo_manager
from core.id_generator import get_id_generator
from ocr.gemini_ocr import get_ocr_service


class TradeService:
    """Trade business logic - Config-driven"""

    def __init__(self):
        self.cfg = config.trade_ledger
        self.repo = RepositoryFactory.get_trade_repository()
        self.fifo_mgr = get_fifo_manager()
        self.ocr_service = get_ocr_service()
        self.id_gen = get_id_generator()

    def create_trade_from_ocr(self, ocr_result: OCRResult) -> Optional[Trade]:
        """Create new trade from OCR result with DETERMINISTIC ID"""
        if not ocr_result.is_valid:
            return None

        if not self.cfg.enabled or not self.cfg.auto_create:
            return None

        # Generate DETERMINISTIC trade ID (not UUID)
        # Format: SYMBOL-YYYYMMDD-NN
        trade_id = self.id_gen.generate(
            symbol=ocr_result.symbol,
            timestamp=datetime.now().timestamp()
        )

        # Get leverage multiplier
        leverage = self.ocr_service.get_leverage_multiplier(
            ocr_result.asset_class,
            ocr_result.symbol
        )

        trade = Trade(
            trade_id=trade_id,  # Human-readable ID
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

        # Create initial entry with deterministic ID
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
        """Get trade by ID"""
        return self.repo.get(trade_id)

    def get_trade_by_symbol(self, symbol: str, status: Optional[str] = 'OPEN') -> Optional[Trade]:
        """Get trade by symbol"""
        trades = self.repo.get_by_symbol(symbol, status)
        return trades[0] if trades else None

    def get_open_trades(self) -> List[Trade]:
        """Get all open trades"""
        return self.repo.get_open_trades()

    def update_trade_status(
        self,
        trade_id: str,
        status: TradeStatus,
        **kwargs
    ) -> Optional[Trade]:
        """Update trade status"""
        trade = self.repo.get(trade_id)
        if not trade:
            return None

        trade.status = status

        if 'current_stop' in kwargs:
            trade.current_stop = kwargs['current_stop']

        self.repo.save(trade)
        return trade

    def add_pyramid_entry(
        self,
        trade_id: str,
        entry_price: float,
        size: float = 0.5
    ) -> Optional[Trade]:
        """Add pyramid entry to trade"""
        trade = self.repo.get(trade_id)
        if not trade:
            return None

        max_pyramids = config.pyramid_settings.get('max_pyramids_per_trade', 5)
        if len(trade.entries) >= max_pyramids:
            return None

        # Generate deterministic entry ID
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

    def calculate_locked_profit(
        self,
        trade: Trade,
        new_stop: float
    ) -> float:
        """Calculate locked profit based on config"""
        if not self.cfg.calculate_locked_profit:
            return 0.0

        calc_cfg = self.cfg.locked_profit_calculation
        if trade.side == 'LONG':
            locked = new_stop - trade.entry_price
        else:
            locked = trade.entry_price - new_stop

        return max(0, locked)

    def cleanup_old_trades(self) -> int:
        """Clean up old closed trades"""
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
        """Get trade statistics"""
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


# Singleton
_trade_service: Optional[TradeService] = None

def get_trade_service() -> TradeService:
    global _trade_service
    if _trade_service is None:
        _trade_service = TradeService()
    return _trade_service

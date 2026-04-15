"""
Repository pattern - SQL-based implementation (PRODUCTION VERSION)
Drop-in replacement for core/repositories.py

Ensures TradeRepository works with new DB schema while maintaining interface.
"""
from typing import Optional, List
from abc import ABC, abstractmethod

from core.models import Trade, MessageMapping, TradeEntry, EntryType, TradeStatus
from core.db import Database, TradeModel, TradeEntryModel, MessageMappingModel
from sqlalchemy import select

class TradeRepository(ABC):
    @abstractmethod
    def save(self, trade: Trade) -> None: pass
    @abstractmethod
    def get(self, trade_id: str) -> Optional[Trade]: pass
    @abstractmethod
    def get_by_symbol(self, symbol: str, status: Optional[str] = None) -> List[Trade]: pass
    @abstractmethod
    def get_open_trades(self) -> List[Trade]: pass
    @abstractmethod
    def delete(self, trade_id: str) -> bool: pass
    @abstractmethod
    def get_all(self) -> List[Trade]: pass

class SQLTradeRepository(TradeRepository):
    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def _model_to_trade(self, trade_model: TradeModel) -> Trade:
        trade = Trade(
            trade_id=trade_model.trade_id,
            symbol=trade_model.symbol,
            asset_class=trade_model.asset_class,
            side=trade_model.side,
            entry_price=0.0,
            status=TradeStatus(trade_model.status) if trade_model.status in [s.value for s in TradeStatus] else TradeStatus.OPEN,
            target=float(trade_model.target) if trade_model.target else None,
            stop_loss=float(trade_model.stop_loss) if trade_model.stop_loss else None,
            created_at=trade_model.created_at.timestamp() if trade_model.created_at else 0
        )

        for entry_model in trade_model.entries:
            entry = TradeEntry(
                entry_id=f"{trade_model.trade_id}-E{entry_model.sequence}",
                entry_price=float(entry_model.entry_price),
                size=float(entry_model.size),
                type=EntryType(entry_model.entry_type) if entry_model.entry_type in [t.value for t in EntryType] else EntryType.INITIAL,
                timestamp=trade_model.created_at.timestamp() if trade_model.created_at else 0,
                closed_size=float(entry_model.closed_size) if entry_model.closed_size else 0.0
            )
            trade.entries.append(entry)

        if trade.entries:
            trade.entry_price = trade.entries[0].entry_price

        if trade_model.snapshot:
            trade.current_stop = float(trade_model.snapshot.current_stop) if trade_model.snapshot.current_stop else None

        return trade

    def save(self, trade: Trade) -> None:
        session = self.db.get_session()
        try:
            trade_model = session.execute(
                select(TradeModel).where(TradeModel.trade_id == trade.trade_id)
            ).scalar_one_or_none()

            if trade_model is None:
                trade_model = TradeModel(
                    trade_id=trade.trade_id,
                    symbol=trade.symbol,
                    side=trade.side,
                    asset_class=trade.asset_class,
                    status=trade.status.value,
                    target=trade.target,
                    stop_loss=trade.stop_loss
                )
                session.add(trade_model)
                session.flush()
            else:
                # Update existing
                trade_model.symbol = trade.symbol
                trade_model.side = trade.side
                trade_model.status = trade.status.value
                trade_model.target = trade.target
                trade_model.stop_loss = trade.stop_loss

            # Delete old entries and recreate
            session.execute(
                select(TradeEntryModel).where(TradeEntryModel.trade_id == trade_model.id)
            )
            # Actually delete them
            for entry_model in list(trade_model.entries):
                session.delete(entry_model)
            session.flush()

            # Create new entries
            for i, entry in enumerate(trade.entries):
                entry_model = TradeEntryModel(
                    trade_id=trade_model.id,
                    entry_price=entry.entry_price,
                    size=entry.size,
                    closed_size=entry.closed_size,
                    entry_type=entry.type.value,
                    sequence=i + 1
                )
                session.add(entry_model)

            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def get(self, trade_id: str) -> Optional[Trade]:
        session = self.db.get_session()
        try:
            trade_model = session.execute(
                select(TradeModel).where(TradeModel.trade_id == trade_id)
            ).scalar_one_or_none()

            if trade_model:
                return self._model_to_trade(trade_model)
            return None
        finally:
            session.close()

    def get_by_symbol(self, symbol: str, status: Optional[str] = None) -> List[Trade]:
        session = self.db.get_session()
        try:
            query = select(TradeModel).where(TradeModel.symbol.ilike(symbol))
            if status:
                query = query.where(TradeModel.status == status)

            trade_models = session.execute(query).scalars().all()
            return [self._model_to_trade(tm) for tm in trade_models]
        finally:
            session.close()

    def get_open_trades(self) -> List[Trade]:
        session = self.db.get_session()
        try:
            trade_models = session.execute(
                select(TradeModel).where(TradeModel.status == "OPEN")
            ).scalars().all()
            return [self._model_to_trade(tm) for tm in trade_models]
        finally:
            session.close()

    def delete(self, trade_id: str) -> bool:
        session = self.db.get_session()
        try:
            trade_model = session.execute(
                select(TradeModel).where(TradeModel.trade_id == trade_id)
            ).scalar_one_or_none()

            if trade_model:
                session.delete(trade_model)
                session.commit()
                return True
            return False
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    def get_all(self) -> List[Trade]:
        session = self.db.get_session()
        try:
            trade_models = session.execute(select(TradeModel)).scalars().all()
            return [self._model_to_trade(tm) for tm in trade_models]
        finally:
            session.close()

class MessageMappingRepository(ABC):
    @abstractmethod
    def save(self, mapping: MessageMapping) -> None: pass
    @abstractmethod
    def get(self, main_msg_id: int) -> Optional[MessageMapping]: pass
    @abstractmethod
    def get_by_trade_id(self, trade_id: str) -> Optional[MessageMapping]: pass
    @abstractmethod
    def get_children(self, parent_msg_id: int) -> List[MessageMapping]: pass
    @abstractmethod
    def get_all(self) -> List[MessageMapping]: pass
    @abstractmethod
    def delete(self, main_msg_id: int) -> bool: pass

class SQLMessageMappingRepository(MessageMappingRepository):
    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def _model_to_mapping(self, model: MessageMappingModel) -> MessageMapping:
        return MessageMapping(
            main_msg_id=int(model.message_id) if model.message_id.isdigit() else 0,
            tg_channel=int(model.channel_id) if model.channel_id and model.channel_id.isdigit() else 0,
            trade_id=model.trade.trade_id if model.trade else None,
            parent_main_msg_id=int(model.parent_main_msg_id) if model.parent_main_msg_id and model.parent_main_msg_id.isdigit() else None,
            parent_tg_msg_id=int(model.parent_tg_msg_id) if model.parent_tg_msg_id and model.parent_tg_msg_id.isdigit() else None,
            created_at=model.created_at.timestamp() if model.created_at else 0
        )

    def save(self, mapping: MessageMapping) -> None:
        session = self.db.get_session()
        try:
            trade_model = None
            if mapping.trade_id:
                trade_model = session.execute(
                    select(TradeModel).where(TradeModel.trade_id == mapping.trade_id)
                ).scalar_one_or_none()

            mapping_model = session.execute(
                select(MessageMappingModel).where(
                    MessageMappingModel.message_id == str(mapping.main_msg_id),
                    MessageMappingModel.platform == "telegram"
                )
            ).scalar_one_or_none()

            if mapping_model is None:
                mapping_model = MessageMappingModel(
                    message_id=str(mapping.main_msg_id),
                    platform="telegram",
                    message_type="main" if not getattr(mapping, 'is_position_update', False) else "update"
                )
                session.add(mapping_model)

            if trade_model:
                mapping_model.trade_id = trade_model.id
                mapping_model.channel_id = str(mapping.tg_channel) if mapping.tg_channel else None
                mapping_model.parent_tg_msg_id = str(mapping.parent_tg_msg_id) if mapping.parent_tg_msg_id else None
                mapping_model.parent_main_msg_id = str(mapping.parent_main_msg_id) if mapping.parent_main_msg_id else None

            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def get(self, main_msg_id: int) -> Optional[MessageMapping]:
        session = self.db.get_session()
        try:
            mapping_model = session.execute(
                select(MessageMappingModel).where(
                    MessageMappingModel.message_id == str(main_msg_id),
                    MessageMappingModel.platform == "telegram"
                )
            ).scalar_one_or_none()

            if mapping_model:
                return self._model_to_mapping(mapping_model)
            return None
        finally:
            session.close()

    def get_by_trade_id(self, trade_id: str) -> Optional[MessageMapping]:
        session = self.db.get_session()
        try:
            trade_model = session.execute(
                select(TradeModel).where(TradeModel.trade_id == trade_id)
            ).scalar_one_or_none()

            if not trade_model:
                return None

            mapping_model = session.execute(
                select(MessageMappingModel).where(
                    MessageMappingModel.trade_id == trade_model.id,
                    MessageMappingModel.platform == "telegram"
                )
            ).scalar_one_or_none()

            if mapping_model:
                return self._model_to_mapping(mapping_model)
            return None
        finally:
            session.close()

    def get_children(self, parent_msg_id: int) -> List[MessageMapping]:
        session = self.db.get_session()
        try:
            mapping_models = session.execute(
                select(MessageMappingModel).where(
                    MessageMappingModel.parent_main_msg_id == str(parent_msg_id),
                    MessageMappingModel.platform == "telegram"
                )
            ).scalars().all()

            return [self._model_to_mapping(mm) for mm in mapping_models]
        finally:
            session.close()

    def get_all(self) -> List[MessageMapping]:
        session = self.db.get_session()
        try:
            mapping_models = session.execute(
                select(MessageMappingModel).where(MessageMappingModel.platform == "telegram")
            ).scalars().all()

            return [self._model_to_mapping(mm) for mm in mapping_models]
        finally:
            session.close()

    def delete(self, main_msg_id: int) -> bool:
        session = self.db.get_session()
        try:
            mapping_model = session.execute(
                select(MessageMappingModel).where(
                    MessageMappingModel.message_id == str(main_msg_id),
                    MessageMappingModel.platform == "telegram"
                )
            ).scalar_one_or_none()

            if mapping_model:
                session.delete(mapping_model)
                session.commit()
                return True
            return False
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

class RepositoryFactory:
    _trade_repo = None
    _mapping_repo = None
    _db = None

    @classmethod
    def get_database(cls):
        if cls._db is None:
            cls._db = Database()
        return cls._db

    @classmethod
    def get_trade_repository(cls):
        if cls._trade_repo is None:
            cls._trade_repo = SQLTradeRepository(cls.get_database())
        return cls._trade_repo

    @classmethod
    def get_mapping_repository(cls):
        if cls._mapping_repo is None:
            cls._mapping_repo = SQLMessageMappingRepository(cls.get_database())
        return cls._mapping_repo

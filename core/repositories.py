"""Repository pattern - SQL-based implementation"""
import json
from typing import Optional, List, Dict, Any
from abc import ABC, abstractmethod

from core.models import Trade, MessageMapping
from core.db import Database, TradeModel, TradeEntryModel, MessageMappingModel

class TradeRepository(ABC):
    @abstractmethod
    def save(self, trade: Trade) -> None:
        pass

    @abstractmethod
    def get(self, trade_id: str) -> Optional[Trade]:
        pass

    @abstractmethod
    def get_by_symbol(self, symbol: str, status: Optional[str] = None) -> List[Trade]:
        pass

    @abstractmethod
    def get_open_trades(self) -> List[Trade]:
        pass

    @abstractmethod
    def delete(self, trade_id: str) -> bool:
        pass

    @abstractmethod
    def get_all(self) -> List[Trade]:
        pass

class SQLTradeRepository(TradeRepository):
    """SQL-based trade repository using SQLAlchemy"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def _model_to_trade(self, trade_model: TradeModel) -> Trade:
        """Convert SQLAlchemy model to domain Trade"""
        trade = Trade(
            trade_id=trade_model.trade_id,
            symbol=trade_model.symbol,
            asset_class=trade_model.asset_class,
            side=trade_model.side,
            entry_price=0.0,
            status=trade_model.status,
            created_at=trade_model.created_at.timestamp() if trade_model.created_at else 0
        )

        # Load entries
        for entry_model in trade_model.entries:
            from core.models import TradeEntry, EntryType
            entry = TradeEntry(
                entry_id=f"{trade_model.trade_id}-E{entry_model.sequence}",
                entry_price=float(entry_model.entry_price),
                size=float(entry_model.size),
                type=EntryType(entry_model.entry_type),
                timestamp=trade_model.created_at.timestamp() if trade_model.created_at else 0,
                closed_size=float(entry_model.closed_size) if entry_model.closed_size else 0.0
            )
            trade.entries.append(entry)

        # Set entry_price from first entry
        if trade.entries:
            trade.entry_price = trade.entries[0].entry_price

        # Load snapshot if exists
        if trade_model.snapshot:
            trade.current_stop = float(trade_model.snapshot.current_stop) if trade_model.snapshot.current_stop else None

        return trade

    def save(self, trade: Trade) -> None:
        session = self.db.get_session()
        try:
            trade_model = session.query(TradeModel).filter_by(trade_id=trade.trade_id).first()

            if trade_model is None:
                trade_model = TradeModel(
                    trade_id=trade.trade_id,
                    symbol=trade.symbol,
                    side=trade.side,
                    asset_class=trade.asset_class,
                    status=trade.status.value
                )
                session.add(trade_model)
                session.flush()

            # Update entries
            session.query(TradeEntryModel).filter_by(trade_id=trade_model.id).delete()

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
            trade_model = session.query(TradeModel).filter_by(trade_id=trade_id).first()
            if trade_model:
                return self._model_to_trade(trade_model)
            return None
        finally:
            session.close()

    def get_by_symbol(self, symbol: str, status: Optional[str] = None) -> List[Trade]:
        session = self.db.get_session()
        try:
            query = session.query(TradeModel).filter(TradeModel.symbol.ilike(symbol))
            if status:
                query = query.filter_by(status=status)

            return [self._model_to_trade(tm) for tm in query.all()]
        finally:
            session.close()

    def get_open_trades(self) -> List[Trade]:
        session = self.db.get_session()
        try:
            trade_models = session.query(TradeModel).filter_by(status="OPEN").all()
            return [self._model_to_trade(tm) for tm in trade_models]
        finally:
            session.close()

    def delete(self, trade_id: str) -> bool:
        session = self.db.get_session()
        try:
            trade_model = session.query(TradeModel).filter_by(trade_id=trade_id).first()
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
            trade_models = session.query(TradeModel).all()
            return [self._model_to_trade(tm) for tm in trade_models]
        finally:
            session.close()

class MessageMappingRepository(ABC):
    @abstractmethod
    def save(self, mapping: MessageMapping) -> None:
        pass

    @abstractmethod
    def get(self, main_msg_id: int) -> Optional[MessageMapping]:
        pass

    @abstractmethod
    def get_by_trade_id(self, trade_id: str) -> Optional[MessageMapping]:
        pass

    @abstractmethod
    def get_children(self, parent_msg_id: int) -> List[MessageMapping]:
        pass

    @abstractmethod
    def get_all(self) -> List[MessageMapping]:
        pass

    @abstractmethod
    def delete(self, main_msg_id: int) -> bool:
        pass

class SQLMessageMappingRepository(MessageMappingRepository):
    """SQL-based message mapping repository"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def save(self, mapping: MessageMapping) -> None:
        session = self.db.get_session()
        try:
            trade_model = None
            if mapping.trade_id:
                trade_model = session.query(TradeModel).filter_by(trade_id=mapping.trade_id).first()

            mapping_model = session.query(MessageMappingModel).filter_by(
                message_id=str(mapping.main_msg_id),
                platform="telegram"
            ).first()

            if mapping_model is None:
                mapping_model = MessageMappingModel(
                    message_id=str(mapping.main_msg_id),
                    platform="telegram",
                    message_type="main" if not mapping.is_position_update else "update"
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
            mapping_model = session.query(MessageMappingModel).filter_by(
                message_id=str(main_msg_id),
                platform="telegram"
            ).first()

            if mapping_model:
                return self._model_to_mapping(mapping_model)
            return None
        finally:
            session.close()

    def get_by_trade_id(self, trade_id: str) -> Optional[MessageMapping]:
        session = self.db.get_session()
        try:
            trade_model = session.query(TradeModel).filter_by(trade_id=trade_id).first()
            if not trade_model:
                return None

            mapping_model = session.query(MessageMappingModel).filter_by(
                trade_id=trade_model.id,
                platform="telegram"
            ).first()

            if mapping_model:
                return self._model_to_mapping(mapping_model)
            return None
        finally:
            session.close()

    def get_children(self, parent_msg_id: int) -> List[MessageMapping]:
        session = self.db.get_session()
        try:
            mapping_models = session.query(MessageMappingModel).filter_by(
                parent_main_msg_id=str(parent_msg_id),
                platform="telegram"
            ).all()

            return [self._model_to_mapping(mm) for mm in mapping_models]
        finally:
            session.close()

    def get_all(self) -> List[MessageMapping]:
        session = self.db.get_session()
        try:
            mapping_models = session.query(MessageMappingModel).filter_by(platform="telegram").all()
            return [self._model_to_mapping(mm) for mm in mapping_models]
        finally:
            session.close()

    def delete(self, main_msg_id: int) -> bool:
        session = self.db.get_session()
        try:
            mapping_model = session.query(MessageMappingModel).filter_by(
                message_id=str(main_msg_id),
                platform="telegram"
            ).first()

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

    def _model_to_mapping(self, model: MessageMappingModel) -> MessageMapping:
        return MessageMapping(
            main_msg_id=int(model.message_id) if model.message_id.isdigit() else 0,
            tg_channel=int(model.channel_id) if model.channel_id and model.channel_id.isdigit() else 0,
            trade_id=model.trade.trade_id if model.trade else None,
            parent_main_msg_id=int(model.parent_main_msg_id) if model.parent_main_msg_id and model.parent_main_msg_id.isdigit() else None,
            parent_tg_msg_id=int(model.parent_tg_msg_id) if model.parent_tg_msg_id and model.parent_tg_msg_id.isdigit() else None,
            created_at=model.created_at.timestamp() if model.created_at else 0
        )

class RepositoryFactory:
    _trade_repo: Optional[TradeRepository] = None
    _mapping_repo: Optional[MessageMappingRepository] = None
    _db: Optional[Database] = None

    @classmethod
    def get_database(cls) -> Database:
        if cls._db is None:
            cls._db = Database()
        return cls._db

    @classmethod
    def get_trade_repository(cls) -> TradeRepository:
        if cls._trade_repo is None:
            cls._trade_repo = SQLTradeRepository(cls.get_database())
        return cls._trade_repo

    @classmethod
    def get_mapping_repository(cls) -> MessageMappingRepository:
        if cls._mapping_repo is None:
            cls._mapping_repo = SQLMessageMappingRepository(cls.get_database())
        return cls._mapping_repo

    @classmethod
    def set_trade_repository(cls, repo: TradeRepository):
        cls._trade_repo = repo

    @classmethod
    def set_mapping_repository(cls, repo: MessageMappingRepository):
        cls._mapping_repo = repo

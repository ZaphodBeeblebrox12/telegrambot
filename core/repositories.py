"""Repository pattern - Database abstraction for future PostgreSQL migration"""
import json
import os
from typing import Optional, List, Dict, Any
from pathlib import Path
from abc import ABC, abstractmethod

from core.models import Trade, MessageMapping
from config.config_loader import config


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


class JSONTradeRepository(TradeRepository):
    def __init__(self, file_path: Optional[str] = None):
        if file_path is None:
            file_path = config.file_paths.get("trade_ledger_file", "trade_ledger.json")
        self.file_path = Path(file_path)
        self._ensure_file()

    def _ensure_file(self):
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "w") as f:
                json.dump({}, f)

    def _load_all(self) -> Dict[str, Any]:
        try:
            with open(self.file_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_all(self, data: Dict[str, Any]):
        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=2)

    def save(self, trade: Trade) -> None:
        data = self._load_all()
        data[trade.trade_id] = trade.to_dict()
        self._save_all(data)

    def get(self, trade_id: str) -> Optional[Trade]:
        data = self._load_all()
        if trade_id in data:
            return Trade.from_dict(data[trade_id])
        return None

    def get_by_symbol(self, symbol: str, status: Optional[str] = None) -> List[Trade]:
        data = self._load_all()
        trades = []
        for trade_data in data.values():
            if trade_data["symbol"].upper() == symbol.upper():
                if status is None or trade_data["status"] == status:
                    trades.append(Trade.from_dict(trade_data))
        return trades

    def get_open_trades(self) -> List[Trade]:
        data = self._load_all()
        return [Trade.from_dict(t) for t in data.values() if t["status"] == "OPEN"]

    def delete(self, trade_id: str) -> bool:
        data = self._load_all()
        if trade_id in data:
            del data[trade_id]
            self._save_all(data)
            return True
        return False

    def get_all(self) -> List[Trade]:
        data = self._load_all()
        return [Trade.from_dict(t) for t in data.values()]


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


class JSONMessageMappingRepository(MessageMappingRepository):
    def __init__(self, file_path: Optional[str] = None):
        if file_path is None:
            file_path = config.file_paths.get("mappings_file", "message_mappings.json")
        self.file_path = Path(file_path)
        self._ensure_file()

    def _ensure_file(self):
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "w") as f:
                json.dump({}, f)

    def _load_all(self) -> Dict[str, Any]:
        try:
            with open(self.file_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_all(self, data: Dict[str, Any]):
        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=2)

    def save(self, mapping: MessageMapping) -> None:
        data = self._load_all()
        data[str(mapping.main_msg_id)] = mapping.to_dict()
        self._save_all(data)

    def get(self, main_msg_id: int) -> Optional[MessageMapping]:
        data = self._load_all()
        key = str(main_msg_id)
        if key in data:
            return MessageMapping.from_dict(data[key])
        return None

    def get_by_trade_id(self, trade_id: str) -> Optional[MessageMapping]:
        data = self._load_all()
        for mapping_data in data.values():
            if mapping_data.get("trade_id") == trade_id:
                return MessageMapping.from_dict(mapping_data)
        return None

    def get_children(self, parent_msg_id: int) -> List[MessageMapping]:
        data = self._load_all()
        children = []
        for mapping_data in data.values():
            if mapping_data.get("parent_main_msg_id") == parent_msg_id:
                children.append(MessageMapping.from_dict(mapping_data))
        return children

    def get_all(self) -> List[MessageMapping]:
        data = self._load_all()
        return [MessageMapping.from_dict(m) for m in data.values()]

    def delete(self, main_msg_id: int) -> bool:
        data = self._load_all()
        key = str(main_msg_id)
        if key in data:
            del data[key]
            self._save_all(data)
            return True
        return False


class RepositoryFactory:
    _trade_repo: Optional[TradeRepository] = None
    _mapping_repo: Optional[MessageMappingRepository] = None

    @classmethod
    def get_trade_repository(cls) -> TradeRepository:
        if cls._trade_repo is None:
            cls._trade_repo = JSONTradeRepository()
        return cls._trade_repo

    @classmethod
    def get_mapping_repository(cls) -> MessageMappingRepository:
        if cls._mapping_repo is None:
            cls._mapping_repo = JSONMessageMappingRepository()
        return cls._mapping_repo

    @classmethod
    def set_trade_repository(cls, repo: TradeRepository):
        cls._trade_repo = repo

    @classmethod
    def set_mapping_repository(cls, repo: MessageMappingRepository):
        cls._mapping_repo = repo

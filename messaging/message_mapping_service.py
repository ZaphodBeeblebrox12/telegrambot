"""Message Mapping Service - SQL-based"""
from typing import Optional, List
from config.config_loader import config
from core.models import MessageMapping
from core.repositories import RepositoryFactory

class MessageMappingService:
    """Manages message mappings for thread tracking"""

    def __init__(self):
        self.cfg = config
        self.repo = RepositoryFactory.get_mapping_repository()

    def create_mapping(
        self,
        main_msg_id: int,
        tg_channel: int,
        trade_id: Optional[str] = None,
        ocr_symbol: Optional[str] = None,
        asset_class: Optional[str] = None,
        leverage_multiplier: int = 1,
        **kwargs
    ) -> MessageMapping:
        mapping = MessageMapping(
            main_msg_id=main_msg_id,
            tg_channel=tg_channel,
            trade_id=trade_id,
            ocr_symbol=ocr_symbol,
            asset_class=asset_class,
            leverage_multiplier=leverage_multiplier
        )
        self.repo.save(mapping)
        return mapping

    def get_mapping(self, main_msg_id: int) -> Optional[MessageMapping]:
        return self.repo.get(main_msg_id)

    def get_mapping_by_trade(self, trade_id: str) -> Optional[MessageMapping]:
        return self.repo.get_by_trade_id(trade_id)

    def get_all_mappings(self) -> List[MessageMapping]:
        return self.repo.get_all()

_mapping_service = None

def get_mapping_service():
    global _mapping_service
    if _mapping_service is None:
        _mapping_service = MessageMappingService()
    return _mapping_service

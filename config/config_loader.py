"""Config-driven configuration loader"""
import json
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class Config:
    """Configuration container with typed sections"""
    system_config: Dict[str, Any]
    destinations: Dict[str, Any]
    message_types: Dict[str, Any]
    command_processing: Dict[str, Any]
    ocr_processing: Dict[str, Any]
    reply_nesting: Dict[str, Any]
    trade_ledger: Dict[str, Any]
    pyramid_settings: Dict[str, Any]
    fifo_settings: Dict[str, Any]
    price_formatting: Dict[str, Any]
    index_behavior: Dict[str, Any]
    message_mapping: Dict[str, Any]
    leverage_settings: Dict[str, Any]
    locked_profit_display: Dict[str, Any]
    position_update_formatting: Dict[str, Any]
    platform_settings: Dict[str, Any]
    performance_settings: Dict[str, Any]
    hashtag_generation: Dict[str, Any]
    admin_settings: Dict[str, Any]
    file_paths: Dict[str, Any]
    watchlist: Dict[str, Any]

    @property
    def ocr(self) -> Dict[str, Any]:
        return self.ocr_processing

    @property
    def destinations_list(self) -> list:
        return list(self.destinations.keys())

    def get_destination(self, dest_id: str) -> Optional[Dict[str, Any]]:
        return self.destinations.get(dest_id)

    def get_message_type(self, msg_type: str) -> Optional[Dict[str, Any]]:
        return self.message_types.get(msg_type)

    def get_command_config(self, command: str) -> Optional[Dict[str, Any]]:
        return self.command_processing.get(command)

def load_config(config_path: str = "config/config.json") -> Config:
    """Load configuration from JSON file"""
    if not os.path.exists(config_path):
        # Try relative paths
        for path in ["config.json", "../config/config.json", "./config/config.json"]:
            if os.path.exists(path):
                config_path = path
                break

    with open(config_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    return Config(
        system_config=data.get('system_config', {}),
        destinations=data.get('destinations', {}),
        message_types=data.get('message_types', {}),
        command_processing=data.get('command_processing', {}),
        ocr_processing=data.get('ocr_processing', {}),
        reply_nesting=data.get('reply_nesting', {}),
        trade_ledger=data.get('trade_ledger', {}),
        pyramid_settings=data.get('pyramid_settings', {}),
        fifo_settings=data.get('fifo_settings', {}),
        price_formatting=data.get('price_formatting', {}),
        index_behavior=data.get('index_behavior', {}),
        message_mapping=data.get('message_mapping', {}),
        leverage_settings=data.get('leverage_settings', {}),
        locked_profit_display=data.get('locked_profit_display', {}),
        position_update_formatting=data.get('position_update_formatting', {}),
        platform_settings=data.get('platform_settings', {}),
        performance_settings=data.get('performance_settings', {}),
        hashtag_generation=data.get('hashtag_generation', {}),
        admin_settings=data.get('admin_settings', {}),
        file_paths=data.get('file_paths', {}),
        watchlist=data.get('watchlist', {})
    )

# Global config instance
config = load_config()

"""Config-driven configuration loader"""
import json
import os
from typing import Dict, Any, Optional

class Config:
    """Configuration container"""
    def __init__(self, data: Dict):
        self._data = data
        self.system_config = data.get('system_config', {})
        self.destinations = data.get('destinations', {})
        self.message_types = data.get('message_types', {})
        self.command_processing = data.get('command_processing', {})
        self.ocr_processing = data.get('ocr_processing', {})
        self.trade_ledger = data.get('trade_ledger', {})
        self.fifo_settings = data.get('fifo_settings', {})
        self.leverage_settings = data.get('leverage_settings', {})
        self.pyramid_settings = data.get('pyramid_settings', {})
        self.price_formatting = data.get('price_formatting', {})

    @property
    def system(self):
        return self.system_config

    def get_message_type(self, msg_type: str) -> Optional[Dict]:
        return self.message_types.get(msg_type)

def load_config(config_path: str = "config/config.json") -> Config:
    if not os.path.exists(config_path):
        for path in ["config.json", "../config/config.json", "./config/config.json"]:
            if os.path.exists(path):
                config_path = path
                break
    with open(config_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return Config(data)

config = load_config()

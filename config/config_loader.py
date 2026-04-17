"""Config Loader with attribute-style access and robust path handling."""
import json
import os
import sys
from typing import Dict, Any, Optional

class ConfigDict(dict):
    """Dictionary that also supports attribute-style access."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"'ConfigDict' has no attribute '{key}'")

    def __setattr__(self, key, value):
        self[key] = value

def _convert_to_config_dict(obj):
    """Recursively convert dicts to ConfigDict."""
    if isinstance(obj, dict):
        return ConfigDict({k: _convert_to_config_dict(v) for k, v in obj.items()})
    elif isinstance(obj, list):
        return [_convert_to_config_dict(item) for item in obj]
    else:
        return obj

class Config:
    """Singleton configuration loaded from JSON."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _find_config_file(self):
        """Search for config.json in multiple possible locations."""
        possible_names = ["config.json", "config.json.txt", "config.JSON"]
        base_dir = os.path.dirname(os.path.abspath(__file__))
        search_dirs = [
            base_dir,
            os.path.dirname(base_dir),
            os.getcwd(),
            os.path.join(os.getcwd(), "config"),
        ]
        
        for directory in search_dirs:
            for name in possible_names:
                path = os.path.join(directory, name)
                if os.path.isfile(path):
                    print(f"✅ Found config file: {path}")
                    return path
        
        print("❌ Could not find config.json. Searched in:")
        for d in search_dirs:
            print(f"   - {d}")
            if os.path.exists(d):
                files = os.listdir(d)
                print(f"     Contents: {[f for f in files if 'config' in f.lower()]}")
        raise FileNotFoundError("config.json not found. Please ensure the file exists in the project root.")

    def _load(self):
        config_path = self._find_config_file()
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except PermissionError:
            print(f"❌ Permission denied reading {config_path}. Try running as administrator or check file permissions.")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON in config file: {e}")
            sys.exit(1)
        
        self._data = _convert_to_config_dict(raw)

    def __getattr__(self, key):
        # Special case: return empty dict for missing 'commands' to avoid crash
        if key == 'commands':
            return {}
        return getattr(self._data, key)

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def get_message_type(self, message_type: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific message type from the 'message_types' section."""
        message_types = self._data.get('message_types', {})
        return message_types.get(message_type)

    @property
    def commands(self) -> Dict[str, Any]:
        """Backward-compatibility: returns command_processing or empty dict."""
        return self._data.get('command_processing', {})

# Singleton instance
config = Config()
"""Config-driven configuration loader - Single Source of Truth"""
import json
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OCRConfig:
    provider: str
    model: str
    prompt: str
    timeout: int
    daily_limit: int
    confidence_threshold: float
    key_management: Dict[str, Any]
    output_mapping: Dict[str, str]
    asset_class_mapping: Dict[str, Any]
    validation_rules: Dict[str, Any]
    symbol_conversion_rules: Dict[str, Any]


@dataclass
class MessageTypeConfig:
    description: str
    platform_rules: Dict[str, bool]
    formatting: Dict[str, str]
    is_nested_reply: bool = False
    parent_type: Optional[str] = None
    requires_media: bool = False
    create_trade_ledger: bool = False
    fifo_format: Optional[Dict[str, Any]] = None
    variables: Optional[Dict[str, str]] = None


@dataclass
class CommandConfig:
    description: str
    action: str
    requires_reply: bool
    delete_command: bool
    output_message_type: Optional[str]
    format_output: bool = False
    allow_media: bool = False
    command_mapping: Optional[Dict[str, Any]] = None
    parse_patterns: Optional[list] = None


@dataclass
class DestinationConfig:
    platform: str
    display_name: str
    platform_id: str
    channel_id: Optional[int] = None
    account_id: Optional[str] = None
    credentials_key: Optional[str] = None


@dataclass
class TradeLedgerConfig:
    enabled: bool
    auto_create: bool
    track_updates: bool
    calculate_locked_profit: bool
    cleanup_days: int
    fields: Dict[str, Any]
    update_types: Dict[str, str]
    locked_profit_calculation: Dict[str, Any]
    fifo_calculation: Dict[str, Any]


@dataclass
class ReplyNestingConfig:
    enabled: bool
    structure: str
    hierarchy_levels: int
    rules: Dict[str, Any]
    admin_channel_behavior: Dict[str, Any]
    target_channel_behavior: Dict[str, Any]
    twitter_behavior: Dict[str, Any]
    mapping_structure: Dict[str, Any]


@dataclass
class PriceFormattingConfig:
    enabled: bool
    trim_trailing_zeros: bool
    max_decimal_places: int
    formats_by_asset: Dict[str, Dict[str, Any]]


@dataclass
class LeverageSettingsConfig:
    enabled: bool
    multipliers: Dict[str, int]
    index_leverage_override: Dict[str, Any]
    tick_sizes: Dict[str, float]
    price_formatting: Dict[str, str]
    display_options: Dict[str, bool]


@dataclass  
class SystemConfig:
    version: str
    admin_channel: int
    bot_name: str
    immutable: bool
    description: str


class ConfigLoader:
    """Singleton config loader that reads config.json as source of truth"""
    _instance = None
    _config: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """Load config.json from file system"""
        config_path = Path(__file__).parent / 'config.json'
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            self._config = json.load(f)

    def reload(self):
        """Reload configuration from disk"""
        self._load_config()

    @property
    def raw(self) -> Dict[str, Any]:
        return self._config

    @property
    def system(self) -> SystemConfig:
        return SystemConfig(**self._config['system_config'])

    @property
    def destinations(self) -> Dict[str, DestinationConfig]:
        return {k: DestinationConfig(**v) for k, v in self._config['destinations'].items()}

    @property
    def message_types(self) -> Dict[str, MessageTypeConfig]:
        result = {}
        for k, v in self._config['message_types'].items():
            result[k] = MessageTypeConfig(**v)
        return result

    @property
    def commands(self) -> Dict[str, CommandConfig]:
        result = {}
        for k, v in self._config['command_processing'].items():
            result[k] = CommandConfig(**v)
        return result

    @property
    def ocr(self) -> OCRConfig:
        cfg = self._config['ocr_processing']
        return OCRConfig(
            provider=cfg['provider'],
            model=cfg['model'],
            prompt=cfg['prompt'],
            timeout=cfg['timeout'],
            daily_limit=cfg['daily_limit'],
            confidence_threshold=cfg['confidence_threshold'],
            key_management=cfg['key_management'],
            output_mapping=cfg['output_mapping'],
            asset_class_mapping=cfg['asset_class_mapping'],
            validation_rules=cfg['validation_rules'],
            symbol_conversion_rules=cfg.get('symbol_conversion_rules', {})
        )

    @property
    def reply_nesting(self) -> ReplyNestingConfig:
        cfg = self._config['reply_nesting']
        return ReplyNestingConfig(
            enabled=cfg['enabled'],
            structure=cfg['structure'],
            hierarchy_levels=cfg['hierarchy_levels'],
            rules=cfg['rules'],
            admin_channel_behavior=cfg['admin_channel_behavior'],
            target_channel_behavior=cfg['target_channel_behavior'],
            twitter_behavior=cfg['twitter_behavior'],
            mapping_structure=cfg['mapping_structure']
        )

    @property
    def trade_ledger(self) -> TradeLedgerConfig:
        cfg = self._config['trade_ledger']
        return TradeLedgerConfig(
            enabled=cfg['enabled'],
            auto_create=cfg['auto_create'],
            track_updates=cfg['track_updates'],
            calculate_locked_profit=cfg['calculate_locked_profit'],
            cleanup_days=cfg['cleanup_days'],
            fields=cfg['fields'],
            update_types=cfg['update_types'],
            locked_profit_calculation=cfg['locked_profit_calculation'],
            fifo_calculation=cfg['fifo_calculation']
        )

    @property
    def price_formatting(self) -> PriceFormattingConfig:
        cfg = self._config['price_formatting']
        return PriceFormattingConfig(
            enabled=cfg['enabled'],
            trim_trailing_zeros=cfg['trim_trailing_zeros'],
            max_decimal_places=cfg['max_decimal_places'],
            formats_by_asset=cfg['formats_by_asset']
        )

    @property
    def leverage_settings(self) -> LeverageSettingsConfig:
        cfg = self._config['leverage_settings']
        return LeverageSettingsConfig(
            enabled=cfg['enabled'],
            multipliers=cfg['multipliers'],
            index_leverage_override=cfg['index_leverage_override'],
            tick_sizes=cfg.get('tick_sizes', {}),
            price_formatting=cfg.get('price_formatting', {}),
            display_options=cfg.get('display_options', {})
        )

    @property
    def platform_settings(self) -> Dict[str, Any]:
        return self._config['platform_settings']

    @property
    def file_paths(self) -> Dict[str, str]:
        return self._config['file_paths']

    @property
    def hashtag_generation(self) -> Dict[str, Any]:
        return self._config['hashtag_generation']

    @property
    def admin_settings(self) -> Dict[str, Any]:
        return self._config['admin_settings']

    @property
    def pyramid_settings(self) -> Dict[str, Any]:
        return self._config['pyramid_settings']

    @property
    def fifo_settings(self) -> Dict[str, Any]:
        return self._config['fifo_settings']

    @property
    def position_update_formatting(self) -> Dict[str, Any]:
        return self._config['position_update_formatting']

    @property
    def locked_profit_display(self) -> Dict[str, Any]:
        return self._config['locked_profit_display']

    @property
    def index_behavior(self) -> Dict[str, Any]:
        return self._config['index_behavior']

    @property
    def message_mapping(self) -> Dict[str, Any]:
        return self._config['message_mapping']

    def get_message_type(self, type_name: str) -> Optional[MessageTypeConfig]:
        cfg = self._config['message_types'].get(type_name)
        if cfg:
            return MessageTypeConfig(**cfg)
        return None

    def get_command(self, command_name: str) -> Optional[CommandConfig]:
        cfg = self._config['command_processing'].get(command_name)
        if cfg:
            return CommandConfig(**cfg)
        return None

    def get_destination(self, dest_id: str) -> Optional[DestinationConfig]:
        cfg = self._config['destinations'].get(dest_id)
        if cfg:
            return DestinationConfig(**cfg)
        return None


config = ConfigLoader()

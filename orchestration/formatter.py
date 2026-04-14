"""Config-driven Message Formatter - Format messages by type and platform"""
from typing import Dict, Any, Optional
from decimal import Decimal

from config.config_loader import config
from core.models import Trade

class MessageFormatter:
    """Formats messages based on config templates"""

    def __init__(self):
        self.cfg = config

    def format_message(
        self,
        message_type: str,
        platform: str,
        variables: Dict[str, Any],
        trade: Optional[Trade] = None
    ) -> str:
        """Format message using config template"""
        msg_config = self.cfg.message_types.get(message_type)
        if not msg_config:
            return self._format_fallback(message_type, variables)

        formatting = msg_config.get('formatting', {})
        template = formatting.get(platform)

        if not template:
            template = formatting.get('telegram', '')

        # Format variables
        formatted_vars = self._format_variables(variables, trade)

        # Apply template
        try:
            return template.format(**formatted_vars)
        except KeyError as e:
            # Fallback: manual replacement
            result = template
            for key, value in formatted_vars.items():
                result = result.replace(f'{{{key}}}', str(value))
            return result

    def _format_variables(
        self,
        variables: Dict[str, Any],
        trade: Optional[Trade]
    ) -> Dict[str, Any]:
        """Format variables for template substitution"""
        formatted = {}

        for key, value in variables.items():
            if isinstance(value, float):
                # Format based on asset class
                if trade:
                    decimal_places = self._get_decimal_places(trade.asset_class)
                    formatted[key] = f"{value:.{decimal_places}f}"
                else:
                    formatted[key] = f"{value:.2f}"
            else:
                formatted[key] = value

        return formatted

    def _get_decimal_places(self, asset_class: str) -> int:
        """Get decimal places for asset class"""
        formats = self.cfg.price_formatting.get('formats_by_asset', {})
        asset_config = formats.get(asset_class, {})
        return asset_config.get('decimal_places', 2)

    def _format_fallback(
        self,
        message_type: str,
        variables: Dict[str, Any]
    ) -> str:
        """Fallback formatting when no config found"""
        lines = [f"** {message_type.upper()} **"]
        for key, value in variables.items():
            lines.append(f"• {key.replace('_', ' ').title()}: {value}")
        return "\n".join(lines)

    def format_price(self, price: float, asset_class: str) -> str:
        """Format price for asset class"""
        decimal_places = self._get_decimal_places(asset_class)
        return f"{price:.{decimal_places}f}"

    def format_percentage(self, value: float, include_sign: bool = True) -> str:
        """Format percentage"""
        if include_sign:
            return f"{value:+.2f}%"
        return f"{value:.2f}%"

    def format_pnl(self, pnl: float, asset_class: str) -> str:
        """Format PnL with appropriate units"""
        locked_cfg = self.cfg.locked_profit_display
        formats = locked_cfg.get('formats', {})
        asset_format = formats.get(asset_class, {})

        unit = asset_format.get('unit', 'points')

        if unit == 'pips':
            return f"{pnl:+.0f} pips"
        elif unit == 'points':
            return f"{pnl:+.0f} points"
        elif unit == 'percent':
            return f"{pnl:+.1f}%"
        elif unit == 'ticks':
            return f"{pnl:+.0f} ticks"

        return f"{pnl:+.2f}"

# Singleton
_formatter: Optional[MessageFormatter] = None

def get_formatter() -> MessageFormatter:
    global _formatter
    if _formatter is None:
        _formatter = MessageFormatter()
    return _formatter

"""Config-driven Message Formatter - All formatting from config"""
from typing import Dict, Any, Optional
from decimal import Decimal, ROUND_HALF_UP

from config.config_loader import config
from core.models import Trade


class MessageFormatter:
    """Formats messages using config-defined templates"""

    def __init__(self):
        self.cfg = config
        self.price_cfg = config.price_formatting
        self.leverage_cfg = config.leverage_settings
        self.position_fmt = config.position_update_formatting

    def format_message(
        self,
        message_type: str,
        platform: str,
        variables: Dict[str, Any],
        trade: Optional[Trade] = None
    ) -> str:
        """Format message using config template"""
        msg_type_cfg = config.get_message_type(message_type)
        if not msg_type_cfg:
            return self._format_fallback(message_type, variables)

        # Get platform-specific format
        template = msg_type_cfg.formatting.get(platform)
        if not template:
            template = msg_type_cfg.formatting.get('telegram', '')

        # Format variables
        formatted_vars = self._prepare_variables(variables, trade, platform)

        # Apply template
        try:
            return template.format(**formatted_vars)
        except KeyError as e:
            # Fallback: replace known variables manually
            result = template
            for key, value in formatted_vars.items():
                result = result.replace(f'{{{key}}}', str(value))
            return result

    def _prepare_variables(
        self,
        variables: Dict[str, Any],
        trade: Optional[Trade],
        platform: str
    ) -> Dict[str, Any]:
        """Prepare variables for formatting"""
        result = dict(variables)

        # Add trade-derived variables
        if trade:
            result.setdefault('symbol', trade.symbol)
            result.setdefault('asset_class', trade.asset_class)
            result.setdefault('side', trade.side)
            result.setdefault('entry', self._format_price(trade.entry_price, trade.asset_class))
            result.setdefault('leverage_multiplier', trade.leverage_multiplier)
            result.setdefault('status', trade.status.value)
            result.setdefault('entries_count', trade.entries_count)
            result.setdefault('weighted_avg_entry', self._format_price(trade.weighted_avg_entry, trade.asset_class))

        # Format price variables
        asset_class = result.get('asset_class', 'UNKNOWN')
        for key in ['price', 'target', 'stop_loss', 'current_stop', 'exit_price']:
            if key in result and result[key] is not None:
                result[key] = self._format_price(float(result[key]), asset_class)

        # Calculate derived values
        if 'price' in result and 'entry' in result and trade:
            try:
                price = float(variables.get('price', 0))
                entry = trade.entry_price

                # Price change
                if trade.side == 'LONG':
                    price_change = ((price - entry) / entry) * 100
                else:
                    price_change = ((entry - price) / entry) * 100

                result['price_change'] = f"{price_change:+.2f}%"

                # Leveraged return
                leverage = trade.leverage_multiplier
                leveraged_return = price_change * leverage
                result['position_return'] = f"{leveraged_return:+.2f}%"

            except (ValueError, TypeError):
                result['price_change'] = "N/A"
                result['position_return'] = "N/A"

        return result

    def _format_price(self, price: float, asset_class: str) -> str:
        """Format price according to config"""
        if not self.price_cfg.enabled:
            return str(price)

        fmt_config = self.price_cfg.formats_by_asset.get(asset_class, {})
        decimal_places = fmt_config.get('decimal_places', 2)
        trim_zeros = fmt_config.get('trim_zeros', True)

        # Format with specified decimals
        formatted = f"{price:.{decimal_places}f}"

        # Trim trailing zeros if configured
        if trim_zeros and '.' in formatted:
            formatted = formatted.rstrip('0').rstrip('.')

        return formatted

    def format_fifo_tree(
        self,
        header: str,
        tree_lines: str,
        booked_pnl: float,
        remaining_size: float,
        weighted_avg: float,
        current_stop: float,
        leverage: int,
        platform: str
    ) -> str:
        """Format FIFO tree from config template"""
        msg_type = config.get_message_type('fifo_close_specific')
        if not msg_type:
            return f"{header}\n{tree_lines}"

        template = msg_type.formatting.get(platform, msg_type.formatting.get('telegram', ''))

        variables = {
            'icon': '½' if 'HALF' in header else '🔹',
            'percentage': '50' if 'HALF' in header else '25',
            'symbol': header.split('|')[1].strip() if '|' in header else '',
            'tree_lines': tree_lines,
            'booked_pnl': f"{booked_pnl:+.2f}",
            'remaining_size': f"{remaining_size:.2f}",
            'weighted_avg': f"{weighted_avg:.2f}",
            'current_stop': f"{current_stop:.2f}",
            'leverage': leverage,
            'status': 'OPEN'
        }

        try:
            return template.format(**variables)
        except KeyError:
            result = template
            for key, value in variables.items():
                result = result.replace(f'{{{key}}}', str(value))
            return result

    def format_position_update(
        self,
        update_type: str,
        symbol: str,
        platform: str,
        **kwargs
    ) -> str:
        """Format position update using config"""
        # Get update type map
        type_map = self.position_fmt.get('update_type_map', {})
        header = type_map.get(update_type, update_type)

        # Get message type config
        msg_type_name = f"{update_type.lower()}_specific"
        msg_type = config.get_message_type(msg_type_name)

        if msg_type:
            template = msg_type.formatting.get(platform, msg_type.formatting.get('telegram', ''))
            try:
                return template.format(symbol=symbol, **kwargs)
            except KeyError:
                pass

        # Fallback formatting
        return self._format_fallback(update_type, {'symbol': symbol, **kwargs})

    def _format_fallback(self, message_type: str, variables: Dict[str, Any]) -> str:
        """Fallback formatting when config not found"""
        lines = [f"Update: {message_type}"]
        for key, value in variables.items():
            if value is not None:
                lines.append(f"• {key}: {value}")
        return "\n".join(lines)

    def get_leverage_multiplier(self, asset_class: str, symbol: str = '') -> int:
        """Get leverage multiplier from config"""
        if not self.leverage_cfg.enabled:
            return 1

        # Check index override
        if asset_class == 'INDEX' and self.leverage_cfg.index_leverage_override.get('enabled'):
            leveraged = self.leverage_cfg.index_leverage_override.get('leveraged_indices', [])
            unleveraged = self.leverage_cfg.index_leverage_override.get('unleveraged_indices', [])

            symbol_clean = symbol.upper().replace(' ', '')

            if any(idx.upper().replace(' ', '') == symbol_clean for idx in leveraged):
                return self.leverage_cfg.index_leverage_override.get('leveraged_indices_multiplier', 20)
            if any(idx.upper().replace(' ', '') == symbol_clean for idx in unleveraged):
                return self.leverage_cfg.index_leverage_override.get('unleveraged_indices_multiplier', 1)

        return self.leverage_cfg.multipliers.get(asset_class, 1)


# Singleton
_formatter: Optional[MessageFormatter] = None

def get_formatter() -> MessageFormatter:
    global _formatter
    if _formatter is None:
        _formatter = MessageFormatter()
    return _formatter

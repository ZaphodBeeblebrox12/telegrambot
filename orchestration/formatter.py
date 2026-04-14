"""Config-driven Message Formatter"""
from typing import Dict, Any, Optional
from config.config_loader import config

class MessageFormatter:
    """Formats messages based on config templates"""

    def __init__(self):
        self.cfg = config

    def format_message(
        self,
        message_type: str,
        platform: str,
        variables: Dict[str, Any],
        trade = None
    ) -> str:
        msg_config = self.cfg.get_message_type(message_type)
        if not msg_config:
            return self._format_fallback(message_type, variables)

        formatting = msg_config.get('formatting', {})
        template = formatting.get(platform, formatting.get('telegram', ''))

        try:
            return template.format(**variables)
        except KeyError:
            result = template
            for key, value in variables.items():
                result = result.replace(f'{{{key}}}', str(value))
            return result

    def _format_fallback(self, message_type: str, variables: Dict[str, Any]) -> str:
        lines = [f"** {message_type.upper()} **"]
        for key, value in variables.items():
            lines.append(f"• {key}: {value}")
        return "\n".join(lines)

_formatter = None

def get_formatter():
    global _formatter
    if _formatter is None:
        _formatter = MessageFormatter()
    return _formatter

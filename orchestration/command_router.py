"""Config-driven Command Router"""
import re
from typing import Optional, Dict, Any
from config.config_loader import config
from core.models import ParsedCommand

class CommandRouter:
    """Routes commands based on config-defined patterns"""

    def __init__(self):
        self.cfg = config
        self.command_patterns = self._compile_patterns()

    def _compile_patterns(self) -> Dict[str, list]:
        patterns = {}
        update_config = config.command_processing.get('/update', {})
        if update_config.get('command_mapping'):
            for cmd in update_config['command_mapping'].keys():
                patterns[cmd] = [{
                    'regex': re.compile(rf'{cmd}\s+(\d+\.?\d*)', re.IGNORECASE),
                    'extract': ['price']
                }, {
                    'regex': re.compile(rf'{cmd}(\d+\.?\d*)', re.IGNORECASE),
                    'extract': ['price']
                }, {
                    'regex': re.compile(rf'{cmd}', re.IGNORECASE),
                    'extract': []
                }]
        return patterns

    def parse_update_command(self, text: str) -> Optional[ParsedCommand]:
        text_upper = text.upper().strip()

        for command, patterns in self.command_patterns.items():
            for pattern_def in patterns:
                match = pattern_def['regex'].search(text_upper)
                if match:
                    parsed = ParsedCommand(
                        command='/update',
                        subcommand=command,
                        raw_text=text
                    )
                    groups = match.groups()
                    extract = pattern_def.get('extract', [])
                    for i, field in enumerate(extract):
                        if i < len(groups):
                            try:
                                if field == 'price':
                                    parsed.price = float(groups[i])
                                elif field == 'percentage':
                                    parsed.percentage = float(groups[i])
                            except ValueError:
                                pass

                    # Extract note text for NOTE command
                    if command == 'NOTE':
                        parts = text.split(' ', 1)
                        if len(parts) > 1:
                            parsed.note_text = parts[1]

                    return parsed
        return None

    def should_delete_command(self, command: str) -> bool:
        return True

_router = None

def get_command_router():
    global _router
    if _router is None:
        _router = CommandRouter()
    return _router

"""Config-driven Command Router - Parses commands using config patterns"""
import re
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

from config.config_loader import config
from core.models import ParsedCommand


class CommandRouter:
    """Routes commands based on config-defined patterns"""

    def __init__(self):
        self.cfg = config
        self.update_config = config.commands.get('/update')
        self.command_patterns = self._compile_patterns()

    def _compile_patterns(self) -> Dict[str, list]:
        """Compile regex patterns from config"""
        patterns = {}
        if self.update_config and self.update_config.parse_patterns:
            for pattern_def in self.update_config.parse_patterns:
                cmd = pattern_def['command']
                if cmd not in patterns:
                    patterns[cmd] = []
                patterns[cmd].append({
                    'regex': re.compile(pattern_def['pattern'], re.IGNORECASE),
                    'extract': pattern_def.get('extract', []),
                    'has_percentage': pattern_def.get('has_percentage', False)
                })
        return patterns

    def parse_update_command(self, text: str) -> Optional[ParsedCommand]:
        """Parse /update command using config patterns"""
        if not self.update_config:
            return None

        text_lower = text.lower().strip()

        # Try each command pattern
        for command, patterns in self.command_patterns.items():
            for pattern_def in patterns:
                match = pattern_def['regex'].search(text_lower)
                if match:
                    return self._build_parsed_command(
                        command=command,
                        match=match,
                        pattern_def=pattern_def,
                        raw_text=text
                    )

        return None

    def _build_parsed_command(
        self,
        command: str,
        match: re.Match,
        pattern_def: Dict[str, Any],
        raw_text: str
    ) -> ParsedCommand:
        """Build ParsedCommand from regex match"""
        extract = pattern_def.get('extract', [])
        groups = match.groups()

        parsed = ParsedCommand(
            command='/update',
            subcommand=command,
            raw_text=raw_text
        )

        # Extract fields based on config
        for i, field in enumerate(extract):
            if i < len(groups):
                value = groups[i]
                if field == 'price':
                    parsed.price = float(value)
                elif field == 'percentage':
                    parsed.percentage = float(value)
                elif field == 'size_percentage':
                    parsed.size_percentage = float(value)
                elif field == 'note_text':
                    parsed.note_text = value
                elif field == 'reason':
                    parsed.reason = value

        # Get message type from command mapping
        if self.update_config and self.update_config.command_mapping:
            cmd_map = self.update_config.command_mapping.get(command, {})
            parsed.message_type = cmd_map.get('type')
            parsed.update_type = cmd_map.get('update_type')

        return parsed

    def get_command_config(self, command: str) -> Optional[Any]:
        """Get command configuration from config"""
        return config.commands.get(command)

    def should_delete_command(self, command: str) -> bool:
        """Check if command should be deleted after processing"""
        cmd_config = self.get_command_config(command)
        if cmd_config:
            return cmd_config.delete_command
        return False

    def get_output_message_type(self, command: str) -> Optional[str]:
        """Get output message type for command"""
        cmd_config = self.get_command_config(command)
        if cmd_config:
            return cmd_config.output_message_type
        return None

    def requires_reply(self, command: str) -> bool:
        """Check if command requires reply to message"""
        cmd_config = self.get_command_config(command)
        if cmd_config:
            return cmd_config.requires_reply
        return False


# Singleton
_router: Optional[CommandRouter] = None

def get_command_router() -> CommandRouter:
    global _router
    if _router is None:
        _router = CommandRouter()
    return _router

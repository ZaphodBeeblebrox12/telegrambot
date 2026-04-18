"""Config-driven Command Router - FIXED
FIXED: Commands must use /update prefix (e.g., "/update closehalf").
FIXED: Exact command matching - no fuzzy matching (targetmet != target).
FIXED: Proper parsing of commands with and without prices.
"""
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
        """Compile regex patterns for command matching.

        Commands must be in format: /update {COMMAND} [price]
        Examples:
        - /update closehalf
        - /update target 4796.87
        - /update closed 4791.17
        - /update trail 4786.78
        """
        patterns = {}
        update_config = config.command_processing.get('/update', {})
        if update_config.get('command_mapping'):
            for cmd in update_config['command_mapping'].keys():
                # Pattern: /update CMD price (e.g., "/update closed 4791.17")
                # Pattern: /update CMD (e.g., "/update closehalf")
                # The command must be exact - no fuzzy matching
                patterns[cmd] = [
                    # Pattern 1: /update CMD PRICE (with space)
                    {
                        'regex': re.compile(rf'/update\s+{cmd}\s+(\d+\.?\d*)', re.IGNORECASE),
                        'extract': ['price']
                    },
                    # Pattern 2: /update CMDPRICE (no space)
                    {
                        'regex': re.compile(rf'/update\s+{cmd}(\d+\.?\d*)', re.IGNORECASE),
                        'extract': ['price']
                    },
                    # Pattern 3: /update CMD (command only, no price)
                    # Uses word boundary to ensure exact match
                    {
                        'regex': re.compile(rf'/update\s+{cmd}\b', re.IGNORECASE),
                        'extract': []
                    }
                ]
        return patterns

    def parse_update_command(self, text: str) -> Optional[ParsedCommand]:
        """Parse command text into ParsedCommand.

        Command format must be: /update {COMMAND} [price]
        - Requires leading /update
        - Command names must be exact (no fuzzy matching)
        - Price is optional for some commands
        """
        text_upper = text.upper().strip()

        # Must start with /update
        if not text_upper.startswith('/UPDATE'):
            return None

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
                        # Remove "/update note" prefix to get the note text
                        prefix_removed = text_upper.replace('/UPDATE NOTE', '', 1).strip()
                        if prefix_removed:
                            # Get original case from input
                            original_text = text
                            prefix_lower = '/update note'
                            if original_text.lower().startswith(prefix_lower):
                                parsed.note_text = original_text[len(prefix_lower):].strip()

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

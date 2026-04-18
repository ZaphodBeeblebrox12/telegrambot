"""Config-driven Command Router - FIXED for exact old bot UX
FIXED: Commands work exactly like old bot:
- /update targetmet (no price)
- /update closehalf (no price)
- /update trail 4800 (with price)
- /update stopped 4750 (with price)
- Case insensitive
- Uses config command_mapping
"""
import re
import logging
from typing import Optional, Dict, Any
from config.config_loader import config
from core.models import ParsedCommand

logger = logging.getLogger(__name__)

class CommandRouter:
    """Routes commands based on config-defined patterns - EXACT old bot behavior"""

    def __init__(self):
        self.cfg = config
        self.command_patterns = self._compile_patterns()
        self.command_mapping = self._load_command_mapping()
        logger.info(f"CommandRouter initialized with patterns: {list(self.command_patterns.keys())}")

    def _load_command_mapping(self) -> Dict[str, Any]:
        """Load command mapping from config"""
        update_config = config.command_processing.get('/update', {})
        mapping = update_config.get('command_mapping', {})
        logger.debug(f"Loaded command mapping: {list(mapping.keys())}")
        return mapping

    def _compile_patterns(self) -> Dict[str, list]:
        """Compile regex patterns for command matching - EXACT old bot behavior.

        Commands must be in format: /update {COMMAND} [price]
        Examples:
        - /update targetmet
        - /update closehalf
        - /update trail 4800
        - /update stopped 4750
        """
        patterns = {}
        update_config = config.command_processing.get('/update', {})
        parse_patterns = update_config.get('parse_patterns', [])

        # Build patterns from config parse_patterns first
        for pattern_def in parse_patterns:
            pattern_str = pattern_def.get('pattern', '')
            command = pattern_def.get('command', '')
            extract = pattern_def.get('extract', [])

            if pattern_str and command:
                try:
                    compiled = re.compile(pattern_str, re.IGNORECASE)
                    if command not in patterns:
                        patterns[command] = []
                    patterns[command].append({
                        'regex': compiled,
                        'extract': extract,
                        'has_percentage': pattern_def.get('has_percentage', False)
                    })
                except re.error as e:
                    logger.error(f"Invalid regex pattern for {command}: {e}")

        # Add fallback patterns for commands without config patterns
        if update_config.get('command_mapping'):
            for cmd in update_config['command_mapping'].keys():
                if cmd not in patterns:
                    # Pattern 1: /update CMD PRICE (with space)
                    # Pattern 2: /update CMD (command only, no price)
                    patterns[cmd] = [
                        {
                            'regex': re.compile(rf'/update\s+{re.escape(cmd)}\s+(\d+\.?\d*)', re.IGNORECASE),
                            'extract': ['price'],
                            'has_percentage': False
                        },
                        {
                            'regex': re.compile(rf'/update\s+{re.escape(cmd)}\b', re.IGNORECASE),
                            'extract': [],
                            'has_percentage': False
                        }
                    ]

        # Add aliases for common command variations
        alias_patterns = {
            'CANCELLED': [
                {'regex': re.compile(r'/update\s+cancel\b', re.IGNORECASE), 'extract': []},
                {'regex': re.compile(r'/update\s+cancelled\s+(.+)', re.IGNORECASE), 'extract': ['reason']},
                {'regex': re.compile(r'/update\s+cancel\s+(.+)', re.IGNORECASE), 'extract': ['reason']},
            ],
            'NOT_TRIGGERED': [
                {'regex': re.compile(r'/update\s+nottriggered\b', re.IGNORECASE), 'extract': []},
                {'regex': re.compile(r'/update\s+not\s+triggered\b', re.IGNORECASE), 'extract': []},
                {'regex': re.compile(r'/update\s+nottriggered\s+(.+)', re.IGNORECASE), 'extract': ['reason']},
                {'regex': re.compile(r'/update\s+not\s+triggered\s+(.+)', re.IGNORECASE), 'extract': ['reason']},
            ],
            'TARGETMET': [
                {'regex': re.compile(r'/update\s+targetmet\b', re.IGNORECASE), 'extract': []},
                {'regex': re.compile(r'/update\s+target\s+met\b', re.IGNORECASE), 'extract': []},
            ],
            'BREAKEVEN': [
                {'regex': re.compile(r'/update\s+be\b', re.IGNORECASE), 'extract': []},
            ],
            'PARTIAL': [
                {'regex': re.compile(r'/update\s+partialclose\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
                {'regex': re.compile(r'/update\s+partialclose\s+(\d+\.?\d*)\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price', 'percentage'], 'has_percentage': True},
            ],
            'CLOSEHALF': [
                {'regex': re.compile(r'/update\s+half\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
                {'regex': re.compile(r'/update\s+close_half\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
            ],
            'TRAIL': [
                {'regex': re.compile(r'/update\s+trailing\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
                {'regex': re.compile(r'/update\s+trailingstop\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
            ],
            'STOPPED': [
                {'regex': re.compile(r'/update\s+stoploss\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
            ],
            'CLOSED': [
                {'regex': re.compile(r'/update\s+close\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
                {'regex': re.compile(r'/update\s+closetrade\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
            ],
            'TARGET': [
                {'regex': re.compile(r'/update\s+targethit\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
            ],
            'UPDATE_STOP': [
                {'regex': re.compile(r'/update\s+newstop\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
                {'regex': re.compile(r'/update\s+updatestop\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
            ],
            'UPDATE_TARGET': [
                {'regex': re.compile(r'/update\s+updatetarget\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
            ],
            'NEWTARGET': [
                {'regex': re.compile(r'/update\s+newtarget\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
            ],
            'PYRAMID': [
                {'regex': re.compile(r'/update\s+pyramid\s+(\d+\.?\d*)\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price', 'size_percentage'], 'has_percentage': True},
                {'regex': re.compile(r'/update\s+pyramid\s+(\d+\.?\d*)', re.IGNORECASE), 'extract': ['price']},
            ],
        }

        for cmd, alias_list in alias_patterns.items():
            if cmd in patterns:
                for alias in alias_list:
                    patterns[cmd].append(alias)
            else:
                patterns[cmd] = alias_list

        return patterns

    def parse_update_command(self, text: str) -> Optional[ParsedCommand]:
        """Parse command text into ParsedCommand - EXACT old bot behavior.

        Command format: /update {COMMAND} [price] [percentage]
        - Case insensitive
        - Price optional for commands that don't need it (targetmet, closehalf, breakeven)
        - Price required for commands that need it (trail, stopped, closed, etc.)
        """
        if not text:
            logger.debug("Empty text, no command parsed")
            return None

        text_clean = text.strip()
        text_upper = text_clean.upper()

        logger.debug(f"Parsing command: '{text_clean}'")

        # Must start with /update
        if not text_upper.startswith('/UPDATE'):
            logger.debug(f"Not an /update command: '{text_clean}'")
            return None

        # Extract command part after /update
        command_part = text_clean[7:].strip()  # Remove '/update'
        if not command_part:
            logger.debug("Empty command after /update")
            return None

        logger.debug(f"Command part: '{command_part}'")

        # Try each command pattern
        for command, pattern_list in self.command_patterns.items():
            for pattern_def in pattern_list:
                match = pattern_def['regex'].search(text_clean)
                if match:
                    logger.info(f"Matched command '{command}' with pattern: {pattern_def['regex'].pattern}")

                    # Build ParsedCommand
                    parsed = ParsedCommand(
                        command='/update',
                        subcommand=command,
                        raw_text=text_clean
                    )

                    # Extract fields
                    groups = match.groups()
                    extract = pattern_def.get('extract', [])

                    for i, field in enumerate(extract):
                        if i < len(groups) and groups[i]:
                            try:
                                value = groups[i].replace(',', '').strip()
                                if field == 'price':
                                    parsed.price = float(value)
                                elif field == 'percentage':
                                    parsed.percentage = float(value)
                                elif field == 'size_percentage':
                                    parsed.size_percentage = float(value)
                                elif field == 'note_text':
                                    # Get original case for note text
                                    original_text = text_clean
                                    prefix = f'/update {command} '
                                    if original_text.lower().startswith(prefix.lower()):
                                        parsed.note_text = original_text[len(prefix):].strip()
                            except ValueError as e:
                                logger.warning(f"Could not parse {field} from '{groups[i]}': {e}")

                    # Handle special cases from command_mapping config
                    cmd_config = self.command_mapping.get(command, {})

                    # Set default percentage from config if specified
                    if cmd_config.get('percentage') and parsed.percentage is None:
                        try:
                            parsed.percentage = float(cmd_config['percentage'])
                        except ValueError:
                            pass

                    # Set default note_text/reason from config
                    if command == 'NOTE' and not parsed.note_text:
                        parsed.note_text = cmd_config.get('default_note_text', '')

                    if command in ['CANCELLED', 'NOT_TRIGGERED'] and not parsed.reason:
                        parsed.reason = cmd_config.get('default_note_text', 'Price never reached entry zone or no longer valid')

                    # Handle TARGETMET -> TARGET conversion (same as old bot)
                    if command == 'TARGETMET':
                        parsed.subcommand = 'TARGET'
                        # Price will be resolved from trade history by executor

                    logger.info(f"Parsed command: {parsed.subcommand}, price={parsed.price}, percentage={parsed.percentage}")
                    return parsed

        logger.warning(f"No pattern matched for: '{text_clean}'")
        return None

    def should_delete_command(self, command: str) -> bool:
        """Check if command message should be deleted after processing"""
        return True

    def list_commands(self) -> list:
        """List all available commands"""
        return list(self.command_patterns.keys())


# Singleton instance
_router = None

def get_command_router():
    global _router
    if _router is None:
        _router = CommandRouter()
    return _router

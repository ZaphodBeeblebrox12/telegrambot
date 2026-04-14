"""
Command Router - Parse text commands into structured actions
"""

import logging
import re
import json
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class CommandParseResult:
    message_type: str
    command: str
    params: Dict[str, Any]


class CommandRouter:
    """Routes text commands to message types and extracts parameters from config."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()
        self.patterns = self._compile_patterns()
        self.command_mapping = self._load_command_mapping()
        logger.info("CommandRouter initialized")

    def _load_config(self) -> Dict:
        """Load configuration from JSON file."""
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return {}

    def _load_command_mapping(self) -> Dict:
        """Load command mapping from config."""
        cmd_config = self.config.get("command_processing", {})
        update_config = cmd_config.get("/update", {})
        return update_config.get("command_mapping", {})

    def _compile_patterns(self) -> list:
        """Compile regex patterns from config."""
        patterns = []
        cmd_config = self.config.get("command_processing", {})
        update_config = cmd_config.get("/update", {})
        parse_patterns = update_config.get("parse_patterns", [])

        for p in parse_patterns:
            try:
                compiled = re.compile(p["pattern"], re.IGNORECASE)
                patterns.append({
                    "regex": compiled,
                    "command": p["command"],
                    "extract": p.get("extract", [])
                })
            except re.error as e:
                logger.error(f"Invalid regex pattern: {p.get('pattern')}: {e}")

        return patterns

    def parse(self, command_text: str) -> Optional[CommandParseResult]:
        """
        Parse command text into structured result using config only.

        Examples:
        - "trail 1.08500" → TRAIL command
        - "close 1.09000" → CLOSE command
        - "partial 1.08000" → PARTIAL command
        """
        if not command_text:
            return None

        text = command_text.strip()
        upper_text = text.upper()

        # Try regex patterns first
        for pattern_def in self.patterns:
            match = pattern_def["regex"].match(text)
            if match:
                command = pattern_def["command"]
                params = {}

                # Extract named groups
                for key in pattern_def["extract"]:
                    try:
                        params[key] = match.group(key)
                    except IndexError:
                        pass

                # Get message type from mapping
                cmd_def = self.command_mapping.get(command, {})
                message_type = cmd_def.get("type", "unknown")

                # Add default percentage if specified
                if "percentage" in cmd_def:
                    params["percentage"] = cmd_def["percentage"]

                logger.debug(f"Matched pattern: {command} with params {params}")
                return CommandParseResult(
                    message_type=message_type,
                    command=command,
                    params=params
                )

        # Try direct command matching
        words = upper_text.split()
        if words:
            first_word = words[0]
            if first_word in self.command_mapping:
                cmd_def = self.command_mapping[first_word]
                params = {}

                # Extract price if present
                if len(words) > 1:
                    try:
                        params["price"] = words[1]
                    except (IndexError, ValueError):
                        pass

                if "percentage" in cmd_def:
                    params["percentage"] = cmd_def["percentage"]

                return CommandParseResult(
                    message_type=cmd_def.get("type", "unknown"),
                    command=first_word,
                    params=params
                )

        logger.warning(f"No pattern matched for: {text}")
        return None

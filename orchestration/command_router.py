"""
CommandRouter - Parse commands from config
"""

import re
import json
import logging
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ParsedCommand:
    command: str
    message_type: str
    params: Dict[str, Any]
    requires_price: bool
    requires_note: bool
    update_type: str


class CommandRouter:
    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self.update_config = self.config.get("command_processing", {}).get("/update", {})
        self.command_mapping = self.update_config.get("command_mapping", {})
        self.parse_patterns = self.update_config.get("parse_patterns", [])

        self._compiled_patterns = []
        for pattern_def in self.parse_patterns:
            try:
                compiled = re.compile(pattern_def["pattern"], re.IGNORECASE)
                self._compiled_patterns.append({
                    "regex": compiled,
                    "command": pattern_def.get("command"),
                    "extract": pattern_def.get("extract", []),
                    "has_percentage": pattern_def.get("has_percentage", False)
                })
            except re.error as e:
                logger.error(f"Invalid regex: {e}")

    def _load_config(self, path: str) -> Dict[str, Any]:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def parse(self, command_text: str) -> Optional[ParsedCommand]:
        if not command_text:
            return None

        command_text = command_text.strip()

        for pattern_def in self._compiled_patterns:
            match = pattern_def["regex"].match(command_text)
            if match:
                return self._build_parsed_command(
                    pattern_def["command"],
                    match,
                    pattern_def["extract"],
                    pattern_def["has_percentage"]
                )

        return None

    def _build_parsed_command(
        self, 
        command: str, 
        match: re.Match,
        extract_fields: List[str],
        has_percentage: bool
    ) -> Optional[ParsedCommand]:

        mapping = self.command_mapping.get(command)
        if not mapping:
            return None

        params = {}
        groups = match.groups()

        for i, field in enumerate(extract_fields):
            if i < len(groups):
                value = groups[i]
                if field in ["price", "percentage", "size_percentage"]:
                    try:
                        params[field] = Decimal(value)
                    except:
                        params[field] = value
                else:
                    params[field] = value

        if has_percentage and "percentage" not in params:
            default_pct = mapping.get("percentage")
            if default_pct:
                params["percentage"] = Decimal(str(default_pct))

        return ParsedCommand(
            command=command,
            message_type=mapping.get("type", "position_update"),
            params=params,
            requires_price=mapping.get("requires_price", False),
            requires_note=mapping.get("requires_note_text", False),
            update_type=mapping.get("update_type", command)
        )

    def validate_command(self, command_text: str) -> Tuple[bool, str]:
        parsed = self.parse(command_text)
        if not parsed:
            return False, "Unknown command"

        mapping = self.command_mapping.get(parsed.command, {})

        if mapping.get("requires_price") and "price" not in parsed.params:
            return False, f"Command requires price"

        return True, "Valid"

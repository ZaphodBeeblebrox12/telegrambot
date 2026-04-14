"""
Message Formatter - Format trade updates for different platforms
"""

import logging
import json
from typing import Dict, Any

logger = logging.getLogger(__name__)

class MessageFormatter:
    """Formats messages according to platform-specific templates."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()
        logger.info("MessageFormatter initialized")

    def _load_config(self) -> Dict:
        """Load configuration from JSON file."""
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return {}

    def format(
        self,
        message_type: str,
        platform: str,
        data: Dict[str, Any]
    ) -> str:
        """Format message for specified platform."""
        msg_config = self.config.get("message_types", {}).get(message_type, {})
        formatting = msg_config.get("formatting", {})

        template = formatting.get(platform, "{trade_id}: Update")

        try:
            # Handle special formatting for partial closes with tree
            if message_type == "partial_close_specific" and "tree_lines" in data:
                fifo_config = msg_config.get("fifo_format", {})
                if fifo_config.get("enabled") and fifo_config.get("use_tree"):
                    return self._format_partial_close_tree(template, data)

            return template.format(**data)
        except KeyError as e:
            logger.error(f"Missing key {e} for formatting {message_type}")
            return f"Update for {data.get('trade_id', 'Unknown')}"
        except Exception as e:
            logger.error(f"Formatting error: {e}")
            return str(data)

    def _format_partial_close_tree(self, template: str, data: Dict[str, Any]) -> str:
        """Format partial close with FIFO tree visualization."""
        trade_id = data.get("trade_id", "Unknown")
        percentage = data.get("percentage", "0")
        pnl = data.get("pnl", "0")
        tree_lines = data.get("tree_lines", "")

        # Build tree format
        lines = [
            f"🔹 PARTIAL CLOSE ({percentage}%)",
            ""
        ]

        if tree_lines:
            lines.extend(tree_lines.split("\n"))

        lines.extend([
            "",
            f"• Booked: {pnl}"
        ])

        return "\n".join(lines)

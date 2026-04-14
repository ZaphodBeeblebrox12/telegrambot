"""
Message Formatter - Format trade updates for different platforms
"""

import logging
import json
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class MessageFormatter:
    """Formats messages according to config-driven templates."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()
        self.message_types = self.config.get("message_types", {})
        logger.info(f"MessageFormatter initialized with {len(self.message_types)} message types")

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
        """Format message for specified platform using config only.

        Args:
            message_type: Key from config.message_types
            platform: Platform name (telegram, twitter, etc.)
            data: Data to format into template
        """
        msg_config = self.message_types.get(message_type, {})

        if not msg_config:
            logger.warning(f"Unknown message_type: {message_type}")
            return f"Update for {data.get('trade_id', 'Unknown')}"

        formatting = msg_config.get("formatting", {})
        template = formatting.get(platform)

        if not template:
            # Fallback to telegram template or generic
            template = formatting.get("telegram", "{trade_id}: Update")

        try:
            # Check for FIFO tree formatting
            fifo_config = msg_config.get("fifo_format", {})
            if fifo_config.get("enabled") and fifo_config.get("use_tree") and "tree_lines" in data:
                return self._format_with_tree(template, data, msg_config)

            return template.format(**data)
        except KeyError as e:
            logger.error(f"Missing key {e} for formatting {message_type}")
            return f"Update for {data.get('trade_id', 'Unknown')}"
        except Exception as e:
            logger.error(f"Formatting error: {e}")
            return str(data)

    def _format_with_tree(
        self,
        template: str,
        data: Dict[str, Any],
        msg_config: Dict[str, Any]
    ) -> str:
        """Format message with FIFO tree visualization from config."""
        # Build tree lines from fifo_result if available
        tree_lines = data.get("tree_lines", "")

        # Create formatted data with tree
        format_data = dict(data)
        if tree_lines:
            format_data["tree_lines"] = tree_lines

        try:
            return template.format(**format_data)
        except KeyError:
            # Fallback to tree format
            lines = [
                f"🔹 PARTIAL CLOSE ({data.get('percentage', '0')}%)",
                ""
            ]
            if tree_lines:
                lines.extend(tree_lines.split("\n"))
            lines.extend(["", f"• Booked: {data.get('pnl', '0')}"])
            return "\n".join(lines)

    def get_supported_platforms(self, message_type: str) -> list:
        """Get list of platforms supported for a message type."""
        msg_config = self.message_types.get(message_type, {})
        platform_rules = msg_config.get("platform_rules", {})
        return [p for p, enabled in platform_rules.items() if enabled]

    def is_platform_supported(self, message_type: str, platform: str) -> bool:
        """Check if a message type is supported on a platform."""
        msg_config = self.message_types.get(message_type, {})
        platform_rules = msg_config.get("platform_rules", {})
        return platform_rules.get(platform, False)

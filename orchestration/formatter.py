"""
Formatter - Message generation from config
"""

import json
import logging
from decimal import Decimal
from typing import Dict, Any

logger = logging.getLogger(__name__)


class MessageFormatter:
    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self.message_types = self.config.get("message_types", {})

    def _load_config(self, path: str) -> Dict[str, Any]:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def format(
        self, 
        message_type: str, 
        platform: str, 
        data: Dict[str, Any]
    ) -> str:
        type_config = self.message_types.get(message_type, {})
        formatting = type_config.get("formatting", {})

        template = formatting.get(platform)
        if not template:
            return f"[{message_type}] {data.get('symbol', 'Unknown')}"

        formatted_data = self._prepare_data(data)

        if type_config.get("fifo_format", {}).get("enabled"):
            formatted_data = self._add_fifo_tree(formatted_data, type_config)

        try:
            return template.format(**formatted_data)
        except KeyError:
            return template

    def _prepare_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        result = data.copy()

        for key in ["entry", "price", "target", "stop_loss", "weighted_avg"]:
            if key in result:
                try:
                    val = Decimal(str(result[key]))
                    result[key] = str(val.quantize(Decimal("0.00001"))).rstrip('0').rstrip('.')
                except:
                    pass

        if "pnl" in result:
            try:
                val = Decimal(str(result["pnl"]))
                result["pnl"] = f"{val:+.2f}"
            except:
                pass

        return result

    def _add_fifo_tree(self, data: Dict[str, Any], type_config: Dict) -> Dict[str, Any]:
        fifo_result = data.get("fifo_result", {})
        fifo_details = fifo_result.get("fifo", [])

        if not fifo_details:
            data["tree_lines"] = ""
            return data

        lines = []
        for i, detail in enumerate(fifo_details):
            prefix = "└─" if i == len(fifo_details) - 1 else "├─"
            entry_seq = detail.get("entry_sequence", i + 1)
            taken = detail.get("taken", "0")
            pnl = detail.get("pnl", "0")
            lines.append(f"{prefix} Exit {entry_seq}: {taken} ({pnl})")

        data["tree_lines"] = "\n".join(lines)
        data["header"] = data.get("update_type", "UPDATE")

        return data

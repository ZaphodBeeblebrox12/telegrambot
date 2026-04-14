"""Gemini OCR Service - Config-driven"""
import os
import json
import re
from typing import Optional, Dict, Any
import google.generativeai as genai

from config.config_loader import config
from core.models import OCRResult

class GeminiOCRService:
    """OCR service using Google's Gemini API - fully config-driven"""

    def __init__(self):
        self.cfg = config.ocr
        self.api_keys = self._load_api_keys()
        self.current_key_index = 0
        self.rate_limited_keys: set = set()
        self._configure_genai()

    def _load_api_keys(self) -> list:
        keys = []
        for i in range(1, 10):
            key = os.getenv(f"GEMINI_API_KEY_{i}") or os.getenv(f"GOOGLE_API_KEY_{i}")
            if key:
                keys.append(key)
        if not keys:
            key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if key:
                keys.append(key)
        return keys

    def _configure_genai(self):
        if self.api_keys:
            genai.configure(api_key=self.api_keys[self.current_key_index])

    def _get_model(self):
        return genai.GenerativeModel(self.cfg.model)

    def _rotate_key(self):
        if len(self.api_keys) <= 1:
            return False
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        if self.current_key_index in self.rate_limited_keys:
            return self._rotate_key()
        self._configure_genai()
        return True

    def _mark_rate_limited(self):
        self.rate_limited_keys.add(self.current_key_index)
        if len(self.rate_limited_keys) >= len(self.api_keys):
            self.rate_limited_keys.clear()

    def process_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> OCRResult:
        if not self.api_keys:
            raise ValueError("No Gemini API keys configured")

        max_retries = self.cfg.key_management.get("max_retries", 3)
        last_error = None

        for attempt in range(max_retries):
            try:
                model = self._get_model()
                image_part = {"mime_type": mime_type, "data": image_bytes}
                response = model.generate_content(
                    [self.cfg.prompt, image_part],
                    generation_config={"temperature": 0.1, "max_output_tokens": 1024},
                    request_options={"timeout": self.cfg.timeout}
                )
                return self._parse_response(response.text)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                if "rate limit" in error_str or "429" in error_str or "quota" in error_str:
                    self._mark_rate_limited()
                    if not self._rotate_key():
                        break
                else:
                    raise

        raise Exception(f"OCR failed after {max_retries} attempts: {last_error}")

    def _parse_response(self, text: str) -> OCRResult:
        json_match = re.search(r"`{3}json\s*(.*?)\s*`{3}", text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = text

        json_str = json_str.strip()
        if json_str.startswith("`"):
            json_str = json_str[1:]
        if json_str.endswith("`"):
            json_str = json_str[:-1]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            data = self._extract_json_like(text)

        mapping = self.cfg.output_mapping
        result = OCRResult(
            symbol=self._get_mapped_value(data, mapping.get("symbol", "symbol")),
            asset_class=self._get_mapped_value(data, mapping.get("asset_class", "asset_class"), "UNKNOWN"),
            setup_found=self._get_mapped_value(data, mapping.get("setup_found", "setup_found"), False),
            side=self._get_mapped_value(data, mapping.get("side", "side")),
            entry=self._get_mapped_value(data, mapping.get("entry", "entry")),
            target=self._get_mapped_value(data, mapping.get("target", "target")),
            stop_loss=self._get_mapped_value(data, mapping.get("stop_loss", "stop_loss")),
            is_stock_chart=self._get_mapped_value(data, mapping.get("is_stock_chart", "is_stock_chart"), False),
            raw_response=text,
            confidence=data.get("confidence", 0.8)
        )

        valid_classes = self.cfg.validation_rules.get("asset_class_values", [])
        if result.asset_class.upper() not in valid_classes:
            result.asset_class = "UNKNOWN"

        return result

    def _get_mapped_value(self, data: Dict, key: str, default=None):
        if key in data:
            return data[key]
        for k, v in data.items():
            if k.lower() == key.lower():
                return v
        return default

    def _extract_json_like(self, text: str) -> Dict[str, Any]:
        result = {}
        patterns = [
            (r'"symbol"\s*:\s*"([^"]+)"', "symbol"),
            (r'"asset_class"\s*:\s*"([^"]+)"', "asset_class"),
            (r'"side"\s*:\s*"([^"]+)"', "side"),
            (r'"entry"\s*:\s*"([^"]+)"', "entry"),
            (r'"target"\s*:\s*"([^"]+)"', "target"),
            (r'"stop_loss"\s*:\s*"([^"]+)"', "stop_loss"),
            (r'"setup_found"\s*:\s*(true|false)', "setup_found"),
        ]
        for pattern, key in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1)
                if key == "setup_found":
                    value = value.lower() == "true"
                result[key] = value
        return result

    def classify_asset(self, symbol: str) -> str:
        symbol_upper = symbol.upper()
        for asset_class, rules in self.cfg.asset_class_mapping.items():
            if asset_class == "UNKNOWN":
                continue
            keywords = rules.get("keywords", [])
            for keyword in keywords:
                if keyword.upper() in symbol_upper:
                    return asset_class
        return "UNKNOWN"

    def get_leverage_multiplier(self, asset_class: str, symbol: str = "") -> int:
        cfg = config.leverage_settings
        if asset_class == "INDEX" and cfg.index_leverage_override.get("enabled"):
            leveraged_indices = cfg.index_leverage_override.get("leveraged_indices", [])
            unleveraged_indices = cfg.index_leverage_override.get("unleveraged_indices", [])
            symbol_clean = symbol.upper().replace(" ", "")
            if any(idx.upper().replace(" ", "") == symbol_clean for idx in leveraged_indices):
                return cfg.index_leverage_override.get("leveraged_indices_multiplier", 20)
            elif any(idx.upper().replace(" ", "") == symbol_clean for idx in unleveraged_indices):
                return cfg.index_leverage_override.get("unleveraged_indices_multiplier", 1)
        return cfg.multipliers.get(asset_class, 1)

_ocr_service: Optional[GeminiOCRService] = None

def get_ocr_service() -> GeminiOCRService:
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = GeminiOCRService()
    return _ocr_service

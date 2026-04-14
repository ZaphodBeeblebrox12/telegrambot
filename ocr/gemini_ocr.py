"""Gemini OCR Service - Config-driven"""
import os
import json
import re
from typing import Optional, Dict, Any
import google.generativeai as genai

from config.config_loader import config
from core.models import OCRResult

class GeminiOCRService:
    """OCR service using Google's Gemini API"""

    def __init__(self):
        self.cfg = config.ocr_processing
        self.api_keys = self._load_api_keys()
        self.current_key_index = 0
        self.rate_limited_keys = set()
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
        return genai.GenerativeModel(self.cfg.get("model", "gemini-2.5-flash"))

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

        max_retries = self.cfg.get("key_management", {}).get("max_retries", 3)
        last_error = None

        for attempt in range(max_retries):
            try:
                model = self._get_model()
                image_part = {"mime_type": mime_type, "data": image_bytes}
                prompt = self.cfg.get("prompt", "Analyze this chart")
                response = model.generate_content(
                    [prompt, image_part],
                    generation_config={"temperature": 0.1, "max_output_tokens": 1024},
                    request_options={"timeout": self.cfg.get("timeout", 30)}
                )
                return self._parse_response(response.text)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                if "rate limit" in error_str or "429" in error_str:
                    self._mark_rate_limited()
                    if not self._rotate_key():
                        break
                else:
                    raise

        raise Exception(f"OCR failed after {max_retries} attempts: {last_error}")

    def _parse_response(self, text: str) -> OCRResult:
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
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

        mapping = self.cfg.get("output_mapping", {})
        result = OCRResult(
            symbol=self._get_value(data, mapping.get("symbol", "symbol")),
            asset_class=self._get_value(data, mapping.get("asset_class", "asset_class"), "UNKNOWN"),
            setup_found=self._get_value(data, mapping.get("setup_found", "setup_found"), False),
            side=self._get_value(data, mapping.get("side", "side")),
            entry=self._get_value(data, mapping.get("entry", "entry")),
            target=self._get_value(data, mapping.get("target", "target")),
            stop_loss=self._get_value(data, mapping.get("stop_loss", "stop_loss")),
            is_stock_chart=self._get_value(data, mapping.get("is_stock_chart", "is_stock_chart"), False),
            raw_response=text,
            confidence=data.get("confidence", 0.8)
        )
        return result

    def _get_value(self, data: Dict, key: str, default=None):
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
            (r'"side"\s*:\s*"([^"]+)"', "side"),
            (r'"entry"\s*:\s*"([^"]+)"', "entry"),
            (r'"target"\s*:\s*"([^"]+)"', "target"),
            (r'"stop_loss"\s*:\s*"([^"]+)"', "stop_loss"),
        ]
        for pattern, key in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result[key] = match.group(1)
        return result

    def get_leverage_multiplier(self, asset_class: str, symbol: str = "") -> int:
        cfg = config.leverage_settings
        return cfg.get("multipliers", {}).get(asset_class, 1)

_ocr_service = None

def get_ocr_service():
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = GeminiOCRService()
    return _ocr_service

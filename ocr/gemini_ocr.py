"""Gemini OCR Service - Config-driven with robust parsing"""
import os
import json
import re
import asyncio
import time
import logging
from typing import Optional, Dict, Any
import google.generativeai as genai

from config.config_loader import config
from core.models import OCRResult

logger = logging.getLogger(__name__)

class GeminiOCRService:
    """OCR service using Google's Gemini API"""

    def __init__(self):
        self.cfg = config.ocr_processing
        self.api_keys = self._load_api_keys()
        self.current_key_index = 0
        self.rate_limited_keys = set()
        self._configure_genai()
        self.timeout = self.cfg.get("timeout", 60)

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
        model_name = self.cfg.get("model", "gemini-2.5-flash")
        try:
            return genai.GenerativeModel(model_name)
        except Exception:
            logger.warning(f"Model {model_name} not available, falling back to gemini-2.0-flash-exp")
            return genai.GenerativeModel("gemini-2.0-flash-exp")

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
                logger.debug(f"Using API key {self.current_key_index+1}/{len(self.api_keys)}")
                response = model.generate_content(
                    [prompt, image_part],
                    generation_config={
                    "temperature": 0.1,
                    "max_output_tokens": 1024,
                    "response_mime_type": "application/json"
                },
                    request_options={"timeout": self.timeout}
                )
                logger.debug(f"Gemini raw response received, length: {len(response.text)}")
                return self._parse_response(response.text)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                if "rate limit" in error_str or "429" in error_str:
                    self._mark_rate_limited()
                    if not self._rotate_key():
                        break
                elif "504" in error_str or "timeout" in error_str or "timed out" in error_str:
                    wait_time = 12 * (attempt + 1)
                    logger.warning(f"Timeout on attempt {attempt+1}/{max_retries}, waiting {wait_time}s before retry")
                    time.sleep(wait_time)
                    continue
                elif "quota" in error_str and "exceeded" in error_str:
                    logger.error("Daily quota exhausted for this API key. Rotating...")
                    self._mark_rate_limited()
                    if not self._rotate_key():
                        raise Exception("All API keys exhausted daily quota.") from e
                    continue
                else:
                    if attempt < max_retries - 1:
                        time.sleep(5)
                        continue
                    else:
                        raise

        raise Exception(f"OCR failed after {max_retries} attempts: {last_error}")

    async def process_image_async(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> OCRResult:
        return await asyncio.to_thread(self.process_image, image_bytes, mime_type)

    def _parse_response(self, text: str) -> OCRResult:
        # Print full response to console (bypass logging truncation)
        print(f"\n[OCR FULL RESPONSE]\n{text}\n[END OCR RESPONSE]\n")
        logger.debug(f"Gemini FULL raw response:\n{text}")

        data = self._extract_json_object(text)

        if data is None:
            logger.warning("Could not extract JSON object, using aggressive field extraction")
            data = self._extract_fields_aggressive(text)

        logger.debug(f"Extracted data for parsing: {data}")

        mapping = self.cfg.get("output_mapping", {})

        def get_val(key: str, default=None):
            if key in data:
                return data[key]
            for k, v in data.items():
                if k.lower() == key.lower():
                    return v
            return default

        setup_found_val = get_val(mapping.get("setup_found", "setup_found"), False)
        if isinstance(setup_found_val, str):
            setup_found_val = setup_found_val.lower() == "true"

        result = OCRResult(
            symbol=get_val(mapping.get("symbol", "symbol")),
            asset_class=get_val(mapping.get("asset_class", "asset_class"), "UNKNOWN"),
            setup_found=setup_found_val,
            side=get_val(mapping.get("side", "side")),
            entry=get_val(mapping.get("entry", "entry")),
            target=get_val(mapping.get("target", "target")),
            stop_loss=get_val(mapping.get("stop_loss", "stop_loss")),
            is_stock_chart=get_val(mapping.get("is_stock_chart", "is_stock_chart"), False),
            raw_response=text,
            confidence=get_val("confidence", 0.8)
        )
        logger.debug(f"Parsed OCR result: setup_found={result.setup_found}, symbol={result.symbol}, side={result.side}, entry={result.entry}")
        return result

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                return None

        json_str = json_str.strip()
        json_str = re.sub(r",\s*}", "}", json_str)
        json_str = re.sub(r",\s*\]", "]", json_str)

        open_braces = json_str.count("{")
        close_braces = json_str.count("}")
        if open_braces > close_braces:
            json_str += "}" * (open_braces - close_braces)

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON decode failed: {e}")
            return None

    def _extract_fields_aggressive(self, text: str) -> Dict[str, Any]:
        result = {}
        patterns = {
            "symbol": [r'"symbol"\s*:\s*"([^"]+)"', r'Symbol[:\s]+([A-Z0-9/]+)'],
            "asset_class": [r'"asset_class"\s*:\s*"([^"]+)"'],
            "setup_found": [r'"setup_found"\s*:\s*(true|false)'],
            "side": [r'"side"\s*:\s*"([^"]+)"', r'(LONG|SHORT)'],
            "entry": [r'"entry"\s*:\s*"?([\d.,]+)"?'],
            "target": [r'"target"\s*:\s*"?([\d.,]+)"?'],
            "stop_loss": [r'"stop_loss"\s*:\s*"?([\d.,]+)"?'],
        }
        for field, regex_list in patterns.items():
            for pattern in regex_list:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    value = match.group(1)
                    if field == "setup_found":
                        result[field] = value.lower() == "true"
                    else:
                        result[field] = value.strip()
                    break
        return result

    def _extract_json_like(self, text: str) -> Dict[str, Any]:
        result = {}
        patterns = [
            (r'"symbol"\s*:\s*"([^"]+)"', "symbol"),
            (r'"side"\s*:\s*"([^"]+)"', "side"),
            (r'"entry"\s*:\s*"?([\d.,]+)"?', "entry"),
            (r'"target"\s*:\s*"?([\d.,]+)"?', "target"),
            (r'"stop_loss"\s*:\s*"?([\d.,]+)"?', "stop_loss"),
            (r'"setup_found"\s*:\s*(true|false)', "setup_found"),
            (r'"asset_class"\s*:\s*"([^"]+)"', "asset_class"),
        ]
        for pattern, key in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1)
                if key == "setup_found":
                    result[key] = value.lower() == "true"
                else:
                    result[key] = value
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

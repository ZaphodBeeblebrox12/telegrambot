"""OCR module - Gemini OCR only (FIX 5: Removed dead OCR)"""
from ocr.gemini_ocr import GeminiOCRService, get_ocr_service

__all__ = ['GeminiOCRService', 'get_ocr_service']

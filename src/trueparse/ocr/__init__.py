"""Optional local OCR backends.

Install with ``pip install trueparse[ocr]``. Everything here degrades to a
no-op when no backend is present, so the base package keeps working unchanged.
"""
from trueparse.ocr.engine import OCREngine, OCRLine, OCRPageResult, ocr_available

__all__ = ["OCREngine", "OCRLine", "OCRPageResult", "ocr_available"]

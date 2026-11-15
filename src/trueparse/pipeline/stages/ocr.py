from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pymupdf as fitz  # PyMuPDF

from trueparse.exceptions import PDFEngineError
from trueparse.extraction.ocr.engine import OCREngine, OCRPageResult
from trueparse.extraction.pdf.renderer import PDFRenderer
from trueparse.models.enums import (
    ErrorCode,
    OCRMode,
)
from trueparse.models.geometry import BoundingBox
from trueparse.models.text import RawTextBlock

if TYPE_CHECKING:
    from trueparse.pipeline.runner import PDFParser

logger = logging.getLogger("trueparse")


def should_ocr(parser: PDFParser, forensics, raw_blocks: list[RawTextBlock]) -> bool:
    if parser.options.ocr == OCRMode.NEVER:
        return False
    if parser.profile.ocr_floor == OCRMode.NEVER and parser.options.ocr != OCRMode.ALWAYS:
        return False
    if not OCREngine.is_available():
        warn_ocr_unavailable_once(
            parser,
        )
        return False
    if parser.options.ocr == OCRMode.ALWAYS:
        return True

    if forensics.likely_scan:
        return True
    if forensics.embedded_images:
        return True  # inspect uncovered regions even when native text exists
    from trueparse.extraction.ocr.recovery import usable_text

    if any(not usable_text(block.text) for block in raw_blocks):
        return True
    if not raw_blocks and forensics.has_native_text:
        return True  # forensic text exists but structured extraction failed
    if forensics.has_native_text:
        return False
    native_chars = sum(b.char_count for b in raw_blocks)
    return native_chars < parser.profile.ocr_min_chars


def warn_ocr_unavailable_once(parser: PDFParser) -> None:
    if parser._ocr_warning_emitted:
        return
    parser._ocr_warning_emitted = True
    message = "OCR was requested but no backend is installed. Install it with: pip install trueparse[ocr]"
    if parser.options.ocr == OCRMode.ALWAYS:
        logger.warning(message)
    else:
        logger.debug(message)


def ocr_page(
    parser: PDFParser,
    page: fitz.Page,
    page_num: int,
    region: BoundingBox | None = None,
    native_blocks: list[RawTextBlock] | None = None,
) -> tuple[list[RawTextBlock], OCRPageResult]:
    parser.budget.reserve_render(page, parser.profile.render_dpi, region, ocr_page=page_num)
    try:
        if region is None:
            image_bytes = parser._timed(
                "ocr_render", PDFRenderer.render_page_to_bytes, page, dpi=parser.profile.render_dpi
            )
        else:
            image_bytes = parser._timed(
                "ocr_render",
                PDFRenderer.render_region_to_bytes,
                page,
                bbox=region,
                dpi=parser.profile.render_dpi,
            )
    except Exception as exc:
        if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
            raise
        logger.error(f"[Page {page_num:02d}] OCR render failed: {exc}")
        parser._page_warnings.setdefault(page_num, []).append(f"Page {page_num}: OCR rendering failed: {exc}")
        return [], OCRPageResult()

    scale = parser.profile.render_dpi / 72.0
    origin = (region.x0, region.y0) if region else (0.0, 0.0)
    result = parser._timed("ocr_recognition", OCREngine.recognize, image_bytes, scale=scale, origin=origin)
    if not result.lines:
        return [], result

    blocks: list[RawTextBlock] = []
    from trueparse.extraction.ocr.recovery import missing_lines

    if page.rotation and region is None:
        for line in result.lines:
            rect = fitz.Rect(*line.bbox.to_list()) * page.derotation_matrix
            line.bbox = BoundingBox.from_rect(tuple(rect))
    filtered = result.model_copy(update={"lines": missing_lines(result.lines, native_blocks or [])})
    for text, bbox, confidence in OCREngine.group_into_blocks(filtered):
        line_count = text.count("\n") + 1
        approx_size = round((bbox.height / max(1, line_count)) * 0.72, 1)
        blocks.append(
            RawTextBlock(
                bbox=bbox,
                text=text,
                spans=[],
                avg_font_size=max(4.0, approx_size),
                is_bold=False,
                page_number=page_num,
                char_count=len(text.replace("\n", "")),
                line_count=line_count,
                fonts=[f"ocr:{result.engine}"],
                ocr_confidence=confidence,
            )
        )

    return blocks, result

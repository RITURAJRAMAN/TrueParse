from __future__ import annotations

from trueparse.config.options import ResourceLimits
from trueparse.exceptions import PDFEngineError
from trueparse.extraction.pdf.renderer import PDFRenderer
from trueparse.models.enums import ErrorCode


class WorkBudget:
    def __init__(self, limits: ResourceLimits):
        self.limits = limits
        self.rendered_pixels = 0
        self.asset_bytes = 0
        self.ocr_pages: set[int] = set()

    def reserve_render(self, page, dpi, bbox=None, *, ocr_page=None):
        pixels = PDFRenderer.pixel_count(page, dpi, bbox)
        if (
            pixels > self.limits.max_render_pixels
            or pixels + self.rendered_pixels > self.limits.max_total_render_pixels
        ):
            raise PDFEngineError(ErrorCode.PDF_RESOURCE_LIMIT, "Rendering exceeds the pixel budget.")
        if ocr_page is not None:
            self.ocr_pages.add(ocr_page)
            if len(self.ocr_pages) > self.limits.max_ocr_pages:
                raise PDFEngineError(ErrorCode.PDF_RESOURCE_LIMIT, "OCR exceeds max_ocr_pages.")
        self.rendered_pixels += pixels

    def reserve_asset(self, size):
        if self.asset_bytes + size > self.limits.max_asset_bytes:
            raise PDFEngineError(ErrorCode.PDF_RESOURCE_LIMIT, "Visual assets exceed max_asset_bytes.")
        self.asset_bytes += size

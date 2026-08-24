from __future__ import annotations
import io
from pathlib import Path
from typing import Optional
import pymupdf as fitz  # PyMuPDF
from PIL import Image

from trueparse.core.models import BoundingBox


class PDFRenderer:
    """Handles high-resolution region clipping for vector visual assets and debug page rendering."""

    @staticmethod
    def render_region_to_bytes(
        page: fitz.Page,
        bbox: BoundingBox,
        dpi: int = 150,
        format: str = "PNG"
    ) -> bytes:
        """Renders only the specified bounding box region of a page to raster image bytes."""
        rect = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
        # Scale matrix for DPI (72 is default PDF point size)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
        return pix.tobytes(output=format.lower())

    @staticmethod
    def render_page_to_bytes(
        page: fitz.Page,
        dpi: int = 150,
        format: str = "PNG"
    ) -> bytes:
        """Renders an entire page for debug visualization only."""
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes(output=format.lower())

    @staticmethod
    def render_page_to_file(
        page: fitz.Page,
        output_path: Path,
        dpi: int = 150,
    ) -> None:
        """Saves page render to file under debug directory."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img_bytes = PDFRenderer.render_page_to_bytes(page, dpi=dpi)
        with open(output_path, "wb") as f:
            f.write(img_bytes)

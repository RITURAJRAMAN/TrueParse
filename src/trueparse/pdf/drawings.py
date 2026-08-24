from __future__ import annotations
from typing import Any, Optional
import pymupdf as fitz  # PyMuPDF
from pydantic import BaseModel, Field

from trueparse.core.models import BoundingBox


class DrawingPath(BaseModel):
    bbox: BoundingBox
    item_count: int
    color: Any = None
    fill: Any = None
    width: Optional[float] = 1.0


class PageDrawings(BaseModel):
    page_number: int
    total_drawings: int
    paths: list[DrawingPath] = Field(default_factory=list)
    clusters: list[BoundingBox] = Field(default_factory=list)


class DrawingInspector:
    """Inspects vector graphics and drawing primitives in PDF pages."""

    @classmethod
    def inspect_page(cls, page: fitz.Page, page_number: int) -> PageDrawings:
        drawings = page.get_drawings() or []
        paths: list[DrawingPath] = []
        clusters: list[BoundingBox] = []

        for d in drawings:
            rect = d.get("rect")
            if not rect:
                continue
            r = fitz.Rect(rect)
            if r.width < 1.0 or r.height < 1.0:
                continue

            bbox = BoundingBox.from_rect((r.x0, r.y0, r.x1, r.y1))
            items = d.get("items") or []
            w = d.get("width")
            w_val = float(w) if w is not None else 1.0

            paths.append(
                DrawingPath(
                    bbox=bbox,
                    item_count=len(items),
                    color=d.get("color"),
                    fill=d.get("fill"),
                    width=w_val,
                )
            )

        # Simple bounding box clustering for vector visual objects (charts, diagrams)
        # Combine overlapping/near drawing boxes that have substantial items
        active_boxes: list[fitz.Rect] = []
        for p in paths:
            # Filter out full-page borders or tiny separator lines
            p_rect = fitz.Rect(p.bbox.x0, p.bbox.y0, p.bbox.x1, p.bbox.y1)
            # Only consider meaningful visual clusters (e.g. area > 400 and not whole page border)
            if p_rect.width > 20 and p_rect.height > 20:
                merged = False
                for idx, existing in enumerate(active_boxes):
                    # If intersects or is close (within 10 pt)
                    expanded = fitz.Rect(existing.x0 - 10, existing.y0 - 10, existing.x1 + 10, existing.y1 + 10)
                    if expanded.intersects(p_rect):
                        active_boxes[idx] = existing | p_rect
                        merged = True
                        break
                if not merged:
                    active_boxes.append(p_rect)

        for b in active_boxes:
            # If large enough to be a diagram or chart
            if b.width >= 40 and b.height >= 40 and (b.width * b.height) < (page.rect.width * page.rect.height * 0.95):
                clusters.append(BoundingBox.from_rect((b.x0, b.y0, b.x1, b.y1)))

        return PageDrawings(
            page_number=page_number,
            total_drawings=len(drawings),
            paths=paths,
            clusters=clusters,
        )

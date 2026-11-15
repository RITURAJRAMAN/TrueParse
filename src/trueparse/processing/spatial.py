from __future__ import annotations

import pymupdf as fitz

from trueparse.models.geometry import BoundingBox


def inside_any(bbox: BoundingBox | None, tables: list, ratio: float) -> bool:
    if not tables or not bbox:
        return False
    rect = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
    area = rect.get_area()
    if area <= 0:
        return False
    for table in tables:
        if not table.bbox:
            continue
        t_rect = fitz.Rect(table.bbox.x0, table.bbox.y0, table.bbox.x1, table.bbox.y1)
        if (rect & t_rect).get_area() > ratio * area:
            return True
    return False

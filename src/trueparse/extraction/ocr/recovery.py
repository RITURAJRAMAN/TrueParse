"""Select missing image regions and suppress OCR duplicates by text AND location."""

from __future__ import annotations

import re

import pymupdf as fitz

from trueparse.extraction.ocr.engine import OCRLine
from trueparse.models.geometry import BoundingBox
from trueparse.models.text import RawTextBlock


def usable_text(text: str) -> bool:
    visible = [char for char in text if not char.isspace()]
    return (
        bool(visible)
        and sum(char.isprintable() and char != "\ufffd" for char in visible) / len(visible) >= 0.85
    )


def missing_regions(
    page, blocks: list[RawTextBlock], forensics, *, always: bool, min_chars: int
) -> list[BoundingBox | None]:
    if always or forensics.likely_scan or any(not usable_text(block.text) for block in blocks):
        return [None]
    if not blocks and not forensics.embedded_images:
        return []  # genuinely blank pages are not scanned documents
    if page.rotation:
        return [None] if forensics.embedded_images else []
    regions: list[BoundingBox | None] = []
    page_area = max(1.0, page.rect.get_area())
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            if rect.get_area() < page_area * 0.08 or rect.width < 72 or rect.height < 24:
                continue  # logos/icons do not justify automatic OCR
            existing_chars = sum(
                len(block.text)
                for block in blocks
                if usable_text(block.text)
                and (rect & fitz.Rect(*block.bbox.to_list())).get_area() > 0.5 * block.bbox.area
            )
            if existing_chars >= max(20, min_chars):
                continue  # native text overlay already covers this image
            candidate = BoundingBox.from_rect(tuple(rect))
            if not any(
                region and (fitz.Rect(*region.to_list()) & rect).get_area() > rect.get_area() * 0.9
                for region in regions
            ):
                regions.append(candidate)
    if not blocks and not regions:
        return [None]
    return regions


def missing_lines(lines: list[OCRLine], native: list[RawTextBlock]) -> list[OCRLine]:
    def key(text):
        return re.sub(r"\W+", "", text).casefold()

    recovered: list[OCRLine] = []
    known = [(key(block.text), block.bbox) for block in native if usable_text(block.text)]
    for line in lines:
        value = key(line.text)
        rect = fitz.Rect(*line.bbox.to_list())
        duplicate = any(
            value
            and value in text
            and (rect & fitz.Rect(*bbox.to_list())).get_area() > 0.5 * min(rect.get_area(), bbox.area)
            for text, bbox in known
        )
        if not duplicate:
            recovered.append(line)
            known.append((value, line.bbox))
    return recovered

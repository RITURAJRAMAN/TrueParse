from __future__ import annotations
from typing import Sequence
from trueparse.core.enums import ElementType
from trueparse.core.models import GenericElement


class ReadingOrderEngine:
    """Sorts and assigns sequential reading order to page elements using vertical-band multi-column layout analysis."""

    @classmethod
    def order_page_elements(
        cls,
        elements: Sequence[GenericElement],
        page_width: float,
        page_height: float,
    ) -> list[GenericElement]:
        if not elements:
            return []

        # 1. Separate headers and footers from main content
        headers: list[GenericElement] = []
        footers: list[GenericElement] = []
        body_elements: list[GenericElement] = []

        for el in elements:
            if el.type == ElementType.HEADER:
                headers.append(el)
            elif el.type in (ElementType.FOOTER, ElementType.PAGE_NUMBER):
                footers.append(el)
            else:
                body_elements.append(el)

        # Sort headers and footers by vertical then horizontal position
        headers.sort(key=lambda e: (e.bbox.y0, e.bbox.x0))
        footers.sort(key=lambda e: (e.bbox.y0, e.bbox.x0))

        if not body_elements:
            final_list = headers + footers
            for idx, el in enumerate(final_list):
                el.reading_order = idx + 1
            return final_list

        # 2. Sort body candidates initially by top coordinate
        body_elements.sort(key=lambda e: (round(e.bbox.y0, 1), e.bbox.x0))

        # 3. Vertical Band Segmentation:
        # Elements spanning the page width or standalone structural block elements (headings, full-width tables)
        # define boundaries between multi-column or single-column bands.
        bands: list[list[GenericElement]] = []
        current_band: list[GenericElement] = []

        for el in body_elements:
            is_spanning = (
                el.bbox.width > page_width * 0.65
                or el.type in (ElementType.SECTION_HEADER, ElementType.TABLE)
            )
            if is_spanning:
                if current_band:
                    bands.append(current_band)
                    current_band = []
                bands.append([el])
            else:
                current_band.append(el)

        if current_band:
            bands.append(current_band)

        # 4. Process each band
        ordered_body: list[GenericElement] = []
        col_threshold = page_width * 0.5

        for band in bands:
            if len(band) <= 1:
                ordered_body.extend(band)
                continue

            # Check if this band has multiple columns
            left_col = [e for e in band if (e.bbox.x0 + e.bbox.x1) / 2.0 < col_threshold]
            right_col = [e for e in band if (e.bbox.x0 + e.bbox.x1) / 2.0 >= col_threshold]

            if left_col and right_col:
                # Multi-column band: process left column top-to-bottom, then right column top-to-bottom
                left_col.sort(key=lambda e: (round(e.bbox.y0, 1), e.bbox.x0))
                right_col.sort(key=lambda e: (round(e.bbox.y0, 1), e.bbox.x0))
                ordered_body.extend(left_col)
                ordered_body.extend(right_col)
            else:
                # Single column band
                band.sort(key=lambda e: (round(e.bbox.y0, 1), e.bbox.x0))
                ordered_body.extend(band)

        final_list = headers + ordered_body + footers

        # 5. Assign sequential reading order numbers (1-indexed)
        for idx, el in enumerate(final_list):
            el.reading_order = idx + 1

        return final_list

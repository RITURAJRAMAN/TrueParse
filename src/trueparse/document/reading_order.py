from __future__ import annotations

from collections.abc import Sequence

from trueparse.core.enums import ElementType
from trueparse.core.models import GenericElement

#: Minimum horizontal whitespace, in points, that counts as a column gutter.
MIN_GUTTER_PT = 12.0

#: An element wider than this fraction of the page spans all columns.
SPANNING_WIDTH_RATIO = 0.65


class ReadingOrderEngine:
    """Restores human reading order using band segmentation and column discovery.

    The page is first cut into horizontal bands at every full-width element.
    Within a band, columns are *discovered* by projecting element x-extents onto
    the x-axis and looking for gutters, rather than assumed to sit either side
    of the page midline. That generalises to three-column journals, asymmetric
    sidebars, and single-column pages alike.
    """

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

        headers.sort(key=lambda e: (e.bbox.y0, e.bbox.x0))
        footers.sort(key=lambda e: (e.bbox.y0, e.bbox.x0))

        if not body_elements:
            final_list = headers + footers
            for idx, el in enumerate(final_list):
                el.reading_order = idx + 1
            return final_list

        # 2. Sort body candidates initially by top coordinate
        body_elements.sort(key=lambda e: (round(e.bbox.y0, 1), e.bbox.x0))

        # 3. Vertical band segmentation: full-width elements and structural
        #    blocks break the page into independently laid-out strips.
        bands = cls._segment_into_bands(body_elements, page_width)

        # 4. Order within each band
        ordered_body: list[GenericElement] = []
        for band in bands:
            ordered_body.extend(cls._order_band(band))

        final_list = headers + ordered_body + footers

        # 5. Assign sequential reading order numbers (1-indexed)
        for idx, el in enumerate(final_list):
            el.reading_order = idx + 1

        return final_list

    @classmethod
    def _segment_into_bands(
        cls,
        body_elements: list[GenericElement],
        page_width: float,
    ) -> list[list[GenericElement]]:
        bands: list[list[GenericElement]] = []
        current_band: list[GenericElement] = []

        for el in body_elements:
            is_spanning = (
                el.bbox.width > page_width * SPANNING_WIDTH_RATIO
                or el.type in (ElementType.SECTION_HEADER, ElementType.TITLE, ElementType.TABLE)
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
        return bands

    @classmethod
    def _order_band(cls, band: list[GenericElement]) -> list[GenericElement]:
        if len(band) <= 1:
            return list(band)

        columns = cls._discover_columns(band)

        if len(columns) <= 1:
            return sorted(band, key=lambda e: (round(e.bbox.y0, 1), e.bbox.x0))

        # Assign each element to the column its horizontal centre falls in,
        # then read the columns left-to-right, each top-to-bottom.
        buckets: list[list[GenericElement]] = [[] for _ in columns]
        for el in band:
            centre = (el.bbox.x0 + el.bbox.x1) / 2.0
            index = cls._column_index(centre, columns)
            buckets[index].append(el)

        ordered: list[GenericElement] = []
        for bucket in buckets:
            bucket.sort(key=lambda e: (round(e.bbox.y0, 1), e.bbox.x0))
            ordered.extend(bucket)
        return ordered

    @staticmethod
    def _discover_columns(band: list[GenericElement]) -> list[tuple[float, float]]:
        """Finds column x-ranges by merging element extents and reading the gaps.

        Returns:
            Left-to-right ``(x0, x1)`` ranges. A single range means the band is
            one column.
        """
        extents = sorted((el.bbox.x0, el.bbox.x1) for el in band)
        if not extents:
            return []

        merged: list[list[float]] = [list(extents[0])]
        for x0, x1 in extents[1:]:
            last = merged[-1]
            # Merge when the horizontal gap is too narrow to be a gutter.
            if x0 - last[1] < MIN_GUTTER_PT:
                last[1] = max(last[1], x1)
            else:
                merged.append([x0, x1])

        return [(m[0], m[1]) for m in merged]

    @staticmethod
    def _column_index(centre: float, columns: list[tuple[float, float]]) -> int:
        """Index of the column containing ``centre``, else the nearest one."""
        for idx, (x0, x1) in enumerate(columns):
            if x0 <= centre <= x1:
                return idx
        return min(
            range(len(columns)),
            key=lambda i: min(abs(centre - columns[i][0]), abs(centre - columns[i][1])),
        )

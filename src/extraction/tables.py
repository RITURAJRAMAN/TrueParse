from __future__ import annotations

import inspect
import logging
from typing import Any

import pymupdf as fitz  # PyMuPDF

from trueparse.exporters.tables import TableRenderer
from trueparse.models.elements import SourceProvenance, TableCell, TableElement
from trueparse.models.enums import ElementType, SourceMethod
from trueparse.models.geometry import BoundingBox

logger = logging.getLogger("trueparse")
_EDGE_TOLERANCE_PT = 2.0
_DUPLICATE_OVERLAP_RATIO = 0.5
_FINDER_OPTIONS = set(inspect.signature(fitz.table.find_tables).parameters)


class NativeTableExtractor:
    @classmethod
    def extract_page_tables(
        cls,
        page: fitz.Page,
        page_number: int,
        start_index: int = 1,
        strategies: list[str] | None = None,
        detect_spans: bool = True,
        warnings: list[str] | None = None,
        render_markdown: bool = True,
        render_html: bool = True,
    ) -> list[TableElement]:
        strategies = strategies or ["lines"]
        found: list[tuple[Any, str]] = []
        drawings = None

        for strategy in strategies:
            # A ruled table requires actual rules. Some PyMuPDF versions also
            # return whitespace layouts for the lines strategy.
            try:
                if drawings is None:
                    drawings = page.get_drawings()
                if strategy == "lines" and not drawings:
                    continue
                finder_options: dict[str, Any] = {"strategy": strategy}
                if "paths" in _FINDER_OPTIONS:
                    finder_options["paths"] = drawings
                if strategy == "lines" and "use_layout" in _FINDER_OPTIONS:
                    # Ruled grids already have explicit boundaries. Do not let
                    # optional layout models replace them with inferred spacer
                    # columns or gate a visible grid out of the result.
                    finder_options["use_layout"] = False
                tabs = page.find_tables(**finder_options)
            except Exception as exc:
                logger.debug(f"Table strategy '{strategy}' failed on page {page_number}: {exc}")
                if warnings is not None:
                    warnings.append(f"Page {page_number}: table strategy '{strategy}' failed: {exc}")
                continue
            if tabs is None and warnings is not None:
                warnings.append(f"Page {page_number}: table strategy '{strategy}' returned no result.")
            if not tabs or not tabs.tables:
                continue

            for tab in tabs.tables:
                if cls._duplicates_existing(tab, [t for t, _ in found]):
                    continue
                found.append((tab, strategy))

        table_elements: list[TableElement] = []
        for idx, (tab, strategy) in enumerate(found):
            try:
                element = cls._build_element(
                    tab=tab,
                    page=page,
                    page_number=page_number,
                    table_idx=start_index + idx,
                    strategy=strategy,
                    detect_spans=detect_spans,
                    page_drawings=drawings,
                    render_markdown=render_markdown,
                    render_html=render_html,
                )
            except Exception as exc:
                if warnings is not None:
                    warnings.append(
                        f"Page {page_number}: table {start_index + idx} reconstruction failed: {exc}"
                    )
                continue
            if element is not None:
                table_elements.append(element)

        return table_elements

    @staticmethod
    def _duplicates_existing(candidate: Any, existing: list[Any]) -> bool:
        """True when ``candidate`` substantially overlaps an accepted table."""
        cand_rect = fitz.Rect(candidate.bbox)
        cand_area = cand_rect.get_area()
        if cand_area <= 0:
            return True
        for other in existing:
            overlap = (cand_rect & fitz.Rect(other.bbox)).get_area()
            if overlap > _DUPLICATE_OVERLAP_RATIO * cand_area:
                return True
        return False

    @classmethod
    def _build_element(
        cls,
        tab: Any,
        page: fitz.Page,
        page_number: int,
        table_idx: int,
        strategy: str,
        detect_spans: bool,
        page_drawings: list[dict] | None = None,
        render_markdown: bool = True,
        render_html: bool = True,
    ) -> TableElement | None:
        elem_id = f"table_p{page_number:04d}_{table_idx:04d}"
        t_bbox = BoundingBox.from_rect(tab.bbox)

        raw_data = tab.extract() or []
        if not raw_data:
            return None

        num_rows = len(raw_data)
        num_cols = max((len(r) for r in raw_data), default=0)
        if num_rows == 0 or num_cols == 0:
            return None

        header_rows = cls._header_row_count(tab)
        cell_rects = cls._cell_rect_grid(tab, num_rows, num_cols)
        spans = cls._compute_spans(cell_rects, num_rows, num_cols) if detect_spans else {}

        if page_drawings is None:
            page_drawings = page.get_drawings() if hasattr(page, "get_drawings") else []
        # Empty grid cells vastly outnumber actual icons in financial reports.
        # Discard line-width geometry once, before testing individual cells.
        page_drawings = [drawing for drawing in page_drawings if drawing["rect"].width > 3]
        cells: list[TableCell] = []

        for r_idx, row in enumerate(raw_data):
            for c_idx in range(num_cols):
                cell_value = row[c_idx] if c_idx < len(row) else None
                val_str = (cell_value or "").strip()
                visual_marker = False
                cell_id = f"cell_p{page_number:04d}_t{table_idx:02d}_r{r_idx:02d}_c{c_idx:02d}"

                cell_bbox = cell_rects[r_idx][c_idx]
                row_span, col_span = spans.get((r_idx, c_idx), (1, 1))

                # A cell covered by a neighbour's span carries no content of
                # its own and is omitted, mirroring HTML colspan/rowspan.
                if (row_span, col_span) == (0, 0):
                    continue

                if not val_str and cell_bbox and page_drawings:
                    val_str = cls._vector_glyph_placeholder(cell_bbox, page_drawings)
                    visual_marker = bool(val_str)

                cells.append(
                    TableCell(
                        id=cell_id,
                        row=r_idx,
                        column=c_idx,
                        page=page_number,
                        row_span=row_span,
                        col_span=col_span,
                        is_header=r_idx < header_rows,
                        text=val_str,
                        kind="visual_marker" if visual_marker else "text",
                        bbox=cell_bbox,
                        confidence=0.95 if strategy == "lines" else 0.82,
                    )
                )

        content_text = "\n".join("\t".join(str(c or "") for c in row) for row in raw_data)

        confidence = 0.96 if strategy == "lines" else 0.84
        table = TableElement(
            id=elem_id,
            type=ElementType.TABLE,
            page=page_number,
            bbox=t_bbox,
            reading_order=1,  # Adjusted by ReadingOrderEngine
            content=content_text,
            confidence=confidence,
            provenance=SourceProvenance(
                method=SourceMethod.GEOMETRIC_ANALYSIS,
                engine=f"pymupdf_tables:{strategy}",
                version=fitz.__version__,
                confidence=confidence,
            ),
            rows=num_rows,
            columns=num_cols,
            cells=cells,
            metadata={"strategy": strategy, "header_rows": header_rows},
        )
        TableRenderer.ensure_representations(table, markdown=render_markdown, html=render_html)
        return table

    @staticmethod
    def _header_row_count(tab: Any) -> int:
        """Number of leading rows PyMuPDF identified as the table header."""
        header = getattr(tab, "header", None)
        if header is None:
            return 1
        names = getattr(header, "names", None)
        if not names:
            return 1
        # ``external`` means the header sits above the grid and is not one of
        # the extracted rows, so no extracted row should be marked as header.
        if getattr(header, "external", False):
            return 0
        return 1

    @staticmethod
    def _cell_rect_grid(
        tab: Any,
        num_rows: int,
        num_cols: int,
    ) -> list[list[BoundingBox | None]]:
        """Builds a rows x cols grid of cell rectangles, with None for gaps."""
        grid: list[list[BoundingBox | None]] = [[None] * num_cols for _ in range(num_rows)]
        rows = getattr(tab, "rows", None) or []
        for r_idx in range(min(num_rows, len(rows))):
            row_cells = getattr(rows[r_idx], "cells", None) or []
            for c_idx in range(min(num_cols, len(row_cells))):
                rect = row_cells[c_idx]
                if rect is None:
                    continue
                grid[r_idx][c_idx] = BoundingBox(
                    x0=float(rect[0]),
                    y0=float(rect[1]),
                    x1=float(rect[2]),
                    y1=float(rect[3]),
                )
        return grid

    @staticmethod
    def _axis_bands(
        intervals_per_slot: list[list[tuple[float, float]]],
    ) -> list[tuple[float, float] | None]:
        """Reduces each row's or column's intervals to its tightest common band.

        Taking ``max(start)`` and ``min(end)`` yields the extent of an *unmerged*
        cell in that slot, because a merged cell's interval is a superset of it.
        """
        bands: list[tuple[float, float] | None] = []
        for intervals in intervals_per_slot:
            if not intervals:
                bands.append(None)
                continue
            start = max(i[0] for i in intervals)
            end = min(i[1] for i in intervals)
            bands.append((start, end) if end - start > _EDGE_TOLERANCE_PT else intervals[0])
        return bands

    @classmethod
    def _compute_spans(
        cls,
        grid: list[list[BoundingBox | None]],
        num_rows: int,
        num_cols: int,
    ) -> dict[tuple[int, int], tuple[int, int]]:
        """Derives row/column spans by measuring cells against the grid bands.

        PyMuPDF marks a merged region by giving the surviving cell a rectangle
        covering the whole region and reporting ``None`` for every position it
        swallows. Counting how many single-row/single-column bands a rectangle
        covers recovers the span, and also handles finders that instead repeat
        the same rectangle in each covered position.

        Covered positions are recorded as ``(0, 0)`` and dropped by the caller.

        Returns:
            ``{(row, col): (row_span, col_span)}`` for every grid position.
        """
        row_intervals: list[list[tuple[float, float]]] = [[] for _ in range(num_rows)]
        col_intervals: list[list[tuple[float, float]]] = [[] for _ in range(num_cols)]
        for r_idx in range(num_rows):
            for c_idx in range(num_cols):
                rect = grid[r_idx][c_idx]
                if rect is None:
                    continue
                row_intervals[r_idx].append((rect.y0, rect.y1))
                col_intervals[c_idx].append((rect.x0, rect.x1))

        row_bands = cls._axis_bands(row_intervals)
        col_bands = cls._axis_bands(col_intervals)

        def covered(bands: list[tuple[float, float] | None], start_idx: int, lo: float, hi: float) -> int:
            """Counts consecutive bands from ``start_idx`` wholly inside ``[lo, hi]``.

            A band must be fully contained to count. Testing only its midpoint
            lets a cell swallow the narrow spacer column beside it, since that
            spacer's midpoint sits within edge tolerance of the shared border.
            """
            count = 0
            for idx in range(start_idx, len(bands)):
                band = bands[idx]
                if band is None:
                    break
                if lo <= band[0] + _EDGE_TOLERANCE_PT and band[1] <= hi + _EDGE_TOLERANCE_PT:
                    count += 1
                else:
                    break
            return max(1, count)

        spans: dict[tuple[int, int], tuple[int, int]] = {}
        consumed: set[tuple[int, int]] = set()

        for r_idx in range(num_rows):
            for c_idx in range(num_cols):
                if (r_idx, c_idx) in consumed:
                    spans[(r_idx, c_idx)] = (0, 0)
                    continue

                rect = grid[r_idx][c_idx]
                if rect is None:
                    # No geometry and not claimed by a neighbour: keep it as an
                    # ordinary empty cell so the grid stays rectangular.
                    spans[(r_idx, c_idx)] = (1, 1)
                    continue

                col_span = covered(col_bands, c_idx, rect.x0, rect.x1)
                row_span = covered(row_bands, r_idx, rect.y0, rect.y1)

                spans[(r_idx, c_idx)] = (row_span, col_span)
                for dr in range(row_span):
                    for dc in range(col_span):
                        if dr or dc:
                            consumed.add((r_idx + dr, c_idx + dc))

        return spans

    @staticmethod
    def _vector_glyph_placeholder(cell_bbox: BoundingBox, page_drawings: list[dict]) -> str:
        """Annotates an uninterpreted drawing in an otherwise empty cell."""
        max_width = cell_bbox.width * 0.85
        for drawing in page_drawings:
            rect = drawing["rect"]
            width = rect.x1 - rect.x0
            if not 3 < width < max_width:
                continue
            height = rect.y1 - rect.y0
            overlap_width = min(rect.x1, cell_bbox.x1) - max(rect.x0, cell_bbox.x0)
            overlap_height = min(rect.y1, cell_bbox.y1) - max(rect.y0, cell_bbox.y0)
            if (
                height > 0
                and overlap_width > 0
                and overlap_height > 0
                and overlap_width * overlap_height > 0.3 * width * height
            ):
                return "[TrueParse vector mark]"
        return ""

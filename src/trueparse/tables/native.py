from __future__ import annotations

import html as html_lib
import logging
from typing import Any

import pymupdf as fitz  # PyMuPDF

from trueparse.core.enums import ElementType, SourceMethod
from trueparse.core.models import (
    BoundingBox,
    SourceProvenance,
    TableCell,
    TableElement,
)

logger = logging.getLogger("trueparse")

#: Two cell edges within this many points are the same grid line.
_EDGE_TOLERANCE_PT = 2.0

#: A 'text' candidate overlapping a 'lines' table by more than this is dropped.
_DUPLICATE_OVERLAP_RATIO = 0.5


class NativeTableExtractor:
    """Extracts tables from PDF pages using PyMuPDF's geometric table finder.

    Ruled tables are found first via the ``lines`` strategy. When a profile
    enables it, the ``text`` strategy then runs to catch whitespace-aligned
    tables (ubiquitous in financial reporting) that have no drawn borders;
    candidates overlapping an already-found table are dropped.
    """

    @classmethod
    def extract_page_tables(
        cls,
        page: fitz.Page,
        page_number: int,
        start_index: int = 1,
        strategies: list[str] | None = None,
        detect_spans: bool = True,
    ) -> list[TableElement]:
        strategies = strategies or ["lines"]
        found: list[tuple[Any, str]] = []

        for strategy in strategies:
            try:
                tabs = page.find_tables(strategy=strategy)
            except Exception as exc:
                logger.debug(f"Table strategy '{strategy}' failed on page {page_number}: {exc}")
                continue
            if not tabs or not tabs.tables:
                continue

            for tab in tabs.tables:
                if cls._duplicates_existing(tab, [t for t, _ in found]):
                    continue
                found.append((tab, strategy))

        table_elements: list[TableElement] = []
        for idx, (tab, strategy) in enumerate(found):
            element = cls._build_element(
                tab=tab,
                page=page,
                page_number=page_number,
                table_idx=start_index + idx,
                strategy=strategy,
                detect_spans=detect_spans,
            )
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
        spans = (
            cls._compute_spans(cell_rects, num_rows, num_cols)
            if detect_spans
            else {}
        )

        page_drawings = page.get_drawings() if hasattr(page, "get_drawings") else []
        cells: list[TableCell] = []

        for r_idx, row in enumerate(raw_data):
            for c_idx in range(num_cols):
                cell_value = row[c_idx] if c_idx < len(row) else None
                val_str = (cell_value or "").strip()
                cell_id = f"cell_p{page_number:04d}_t{table_idx:02d}_r{r_idx:02d}_c{c_idx:02d}"

                cell_bbox = cell_rects[r_idx][c_idx]
                row_span, col_span = spans.get((r_idx, c_idx), (1, 1))

                # A cell covered by a neighbour's span carries no content of
                # its own and is omitted, mirroring HTML colspan/rowspan.
                if (row_span, col_span) == (0, 0):
                    continue

                if not val_str and cell_bbox and page_drawings:
                    val_str = cls._vector_glyph_placeholder(cell_bbox, page_drawings)

                cells.append(
                    TableCell(
                        id=cell_id,
                        row=r_idx,
                        column=c_idx,
                        row_span=row_span,
                        col_span=col_span,
                        is_header=r_idx < header_rows,
                        text=val_str,
                        bbox=cell_bbox,
                        confidence=0.95 if strategy == "lines" else 0.82,
                    )
                )

        markdown_str = cls._to_markdown(raw_data, num_cols)
        html_str = cls._to_html(cells, num_rows, header_rows)
        content_text = "\n".join(
            "\t".join(str(c or "") for c in row) for row in raw_data
        )

        confidence = 0.96 if strategy == "lines" else 0.84
        return TableElement(
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
            markdown=markdown_str,
            html=html_str,
            metadata={"strategy": strategy, "header_rows": header_rows},
        )

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
        grid: list[list[BoundingBox | None]] = [
            [None] * num_cols for _ in range(num_rows)
        ]
        rows = getattr(tab, "rows", None) or []
        for r_idx in range(min(num_rows, len(rows))):
            row_cells = getattr(rows[r_idx], "cells", None) or []
            for c_idx in range(min(num_cols, len(row_cells))):
                rect = row_cells[c_idx]
                if rect is None:
                    continue
                grid[r_idx][c_idx] = BoundingBox(
                    x0=float(rect[0]), y0=float(rect[1]),
                    x1=float(rect[2]), y1=float(rect[3]),
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

        def covered(bands: list[tuple[float, float] | None],
                    start_idx: int, lo: float, hi: float) -> int:
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
                if (lo <= band[0] + _EDGE_TOLERANCE_PT
                        and band[1] <= hi + _EDGE_TOLERANCE_PT):
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
        """Marks cells whose only content is a drawn tick/cross icon."""
        cb_rect = fitz.Rect(cell_bbox.x0, cell_bbox.y0, cell_bbox.x1, cell_bbox.y1)
        has_vector_icon = any(
            (d["rect"] & cb_rect).get_area() > 0.3 * d["rect"].get_area()
            and d["rect"].width < cb_rect.width * 0.85
            and d["rect"].width > 3
            for d in page_drawings
        )
        return "[X]" if has_vector_icon else ""

    @staticmethod
    def _to_markdown(raw_data: list[list[Any]], num_cols: int) -> str:
        """Renders a Markdown table. Spans are flattened; Markdown has none."""
        if not raw_data:
            return ""

        def clean(value: Any) -> str:
            return str(value or "").replace("|", "\\|").replace("\n", " ").strip()

        def pad(row: list[Any]) -> list[str]:
            cells = [clean(c) for c in row]
            cells.extend([""] * (num_cols - len(cells)))
            return cells[:num_cols]

        lines = [
            "| " + " | ".join(pad(raw_data[0])) + " |",
            "| " + " | ".join(["---"] * num_cols) + " |",
        ]
        for row in raw_data[1:]:
            lines.append("| " + " | ".join(pad(row)) + " |")
        return "\n".join(lines)

    @staticmethod
    def _to_html(
        cells: list[TableCell],
        num_rows: int,
        header_rows: int,
    ) -> str:
        """Renders HTML preserving real ``colspan``/``rowspan`` attributes."""
        by_row: dict[int, list[TableCell]] = {}
        for cell in cells:
            by_row.setdefault(cell.row, []).append(cell)

        parts: list[str] = ["<table>"]
        in_head = header_rows > 0
        if in_head:
            parts.append("  <thead>")

        for r_idx in range(num_rows):
            if in_head and r_idx == header_rows:
                parts.append("  </thead>")
                parts.append("  <tbody>")
                in_head = False
            elif not in_head and r_idx == header_rows and header_rows == 0:
                parts.append("  <tbody>")

            parts.append("    <tr>")
            tag = "th" if r_idx < header_rows else "td"
            for cell in sorted(by_row.get(r_idx, []), key=lambda c: c.column):
                attrs = ""
                if cell.col_span > 1:
                    attrs += f' colspan="{cell.col_span}"'
                if cell.row_span > 1:
                    attrs += f' rowspan="{cell.row_span}"'
                parts.append(f"      <{tag}{attrs}>{html_lib.escape(cell.text)}</{tag}>")
            parts.append("    </tr>")

        if in_head:
            parts.append("  </thead>")
        elif header_rows > 0 or num_rows > 0:
            parts.append("  </tbody>")
        parts.append("</table>")
        return "\n".join(parts)

    @classmethod
    def merge_cross_page_tables(
        cls,
        pages_elements: list[list[Any]],
        column_tolerance_pt: float = 6.0,
    ) -> list[list[Any]]:
        """Folds a table continuing onto the next page into its predecessor.

        A continuation is recognised when the last table on page N and the
        first table on page N+1 have the same column count and their column
        x-positions line up within ``column_tolerance_pt``. The continuation's
        rows are appended and its header row, if it repeats, is dropped.

        Args:
            pages_elements: Per-page element lists in reading order.
            column_tolerance_pt: Allowed drift between column left edges.

        Returns:
            The same structure with continuation tables removed.
        """
        last_table: TableElement | None = None

        for elements in pages_elements:
            page_tables = [e for e in elements if isinstance(e, TableElement)]
            if not page_tables:
                last_table = None
                continue

            first = page_tables[0]
            if last_table is not None and cls._continues(last_table, first, column_tolerance_pt):
                cls._absorb(last_table, first)
                elements.remove(first)
                page_tables = page_tables[1:]

            last_table = page_tables[-1] if page_tables else None

        for elements in pages_elements:
            for idx, element in enumerate(elements):
                element.reading_order = idx + 1
        return pages_elements

    @staticmethod
    def _column_origins(table: TableElement) -> list[float]:
        origins: dict[int, float] = {}
        for cell in table.cells:
            if cell.bbox is not None and cell.column not in origins:
                origins[cell.column] = cell.bbox.x0
        return [origins[k] for k in sorted(origins)]

    @classmethod
    def _continues(
        cls,
        first: TableElement,
        second: TableElement,
        tolerance: float,
    ) -> bool:
        if first.columns != second.columns:
            return False
        a, b = cls._column_origins(first), cls._column_origins(second)
        if len(a) != len(b) or not a:
            return False
        return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))

    @classmethod
    def _absorb(cls, target: TableElement, continuation: TableElement) -> None:
        """Appends ``continuation``'s rows onto ``target``."""
        target_headers = {c.text.strip() for c in target.cells if c.is_header and c.text.strip()}
        cont_headers = {c.text.strip() for c in continuation.cells if c.is_header and c.text.strip()}
        repeats_header = bool(target_headers) and target_headers == cont_headers

        skip_rows = 1 if repeats_header else 0
        row_offset = target.rows

        for cell in continuation.cells:
            if cell.row < skip_rows:
                continue
            new_row = row_offset + cell.row - skip_rows
            target.cells.append(
                cell.model_copy(
                    update={
                        "id": f"{target.id}_cont_r{new_row:02d}_c{cell.column:02d}",
                        "row": new_row,
                        "is_header": False,
                    }
                )
            )

        added_rows = continuation.rows - skip_rows
        target.rows += max(0, added_rows)
        target.content = f"{target.content}\n{continuation.content}"

        if target.markdown and continuation.markdown:
            cont_lines = continuation.markdown.split("\n")
            # Drop the continuation's own header and separator rows.
            body = cont_lines[2:] if len(cont_lines) > 2 else []
            if body:
                target.markdown = target.markdown + "\n" + "\n".join(body)

        target.html = cls._to_html(
            target.cells,
            target.rows,
            header_rows=1 if target_headers else 0,
        )
        target.metadata.setdefault("continued_from_pages", []).append(continuation.page)

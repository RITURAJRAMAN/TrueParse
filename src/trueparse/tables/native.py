from __future__ import annotations
from typing import Optional
import pymupdf as fitz  # PyMuPDF

from trueparse.core.enums import ElementType, SourceMethod
from trueparse.core.models import (
    BoundingBox,
    TableCell,
    TableElement,
    SourceProvenance,
)


class NativeTableExtractor:
    """Extracts tables from PDF pages using PyMuPDF's geometric table finder."""

    @classmethod
    def extract_page_tables(
        cls,
        page: fitz.Page,
        page_number: int,
        start_index: int = 1,
    ) -> list[TableElement]:
        table_elements: list[TableElement] = []

        try:
            tabs = page.find_tables()
        except Exception:
            return []

        if not tabs or not tabs.tables:
            return []

        for idx, tab in enumerate(tabs.tables):
            table_idx = start_index + idx
            elem_id = f"table_p{page_number:04d}_{table_idx:04d}"
            t_bbox = BoundingBox.from_rect(tab.bbox)
            
            # tab.extract() returns a list of rows, each a list of cell texts
            raw_data = tab.extract() or []
            if not raw_data:
                continue

            num_rows = len(raw_data)
            num_cols = max((len(r) for r in raw_data), default=0)
            if num_rows == 0 or num_cols == 0:
                continue

            cells: list[TableCell] = []

            page_drawings = page.get_drawings() if hasattr(page, "get_drawings") else []

            # Standard cell grid iteration
            for r_idx, row in enumerate(raw_data):
                is_header_row = (r_idx == 0)
                tab_row_cells = tab.rows[r_idx].cells if (hasattr(tab, 'rows') and r_idx < len(tab.rows)) else []
                for c_idx, cell_value in enumerate(row):
                    val_str = (cell_value or "").strip()
                    cell_id = f"cell_p{page_number:04d}_t{table_idx:02d}_r{r_idx:02d}_c{c_idx:02d}"

                    cell_bbox = None
                    if c_idx < len(tab_row_cells) and tab_row_cells[c_idx] is not None:
                        cb = tab_row_cells[c_idx]
                        cell_bbox = BoundingBox(x0=float(cb[0]), y0=float(cb[1]), x1=float(cb[2]), y1=float(cb[3]))

                    # Check for visual checkmarks / icon vectors inside cell
                    if not val_str and cell_bbox and page_drawings:
                        cb_rect = fitz.Rect(cell_bbox.x0, cell_bbox.y0, cell_bbox.x1, cell_bbox.y1)
                        has_vector_icon = any(
                            (d['rect'] & cb_rect).get_area() > 0.3 * d['rect'].get_area()
                            and d['rect'].width < cb_rect.width * 0.85
                            and d['rect'].width > 3
                            for d in page_drawings
                        )
                        if has_vector_icon:
                            val_str = "[X]"

                    cells.append(
                        TableCell(
                            id=cell_id,
                            row=r_idx,
                            column=c_idx,
                            row_span=1,
                            col_span=1,
                            is_header=is_header_row,
                            text=val_str,
                            bbox=cell_bbox,
                            confidence=0.95,
                        )
                    )

            # Generate Markdown representation
            md_lines: list[str] = []
            if raw_data:
                header = [str(c or "").replace("|", "\\|").strip() for c in raw_data[0]]
                md_lines.append("| " + " | ".join(header) + " |")
                md_lines.append("| " + " | ".join(["---"] * len(header)) + " |")
                for row in raw_data[1:]:
                    row_clean = [str(c or "").replace("|", "\\|").strip() for c in row]
                    # pad if needed
                    while len(row_clean) < len(header):
                        row_clean.append("")
                    md_lines.append("| " + " | ".join(row_clean[:len(header)]) + " |")
            markdown_str = "\n".join(md_lines)

            # Generate HTML representation
            html_parts: list[str] = ["<table>"]
            if raw_data:
                html_parts.append("  <thead><tr>")
                for c in raw_data[0]:
                    html_parts.append(f"    <th>{c or ''}</th>")
                html_parts.append("  </tr></thead>")
                if len(raw_data) > 1:
                    html_parts.append("  <tbody>")
                    for row in raw_data[1:]:
                        html_parts.append("    <tr>")
                        for c in row:
                            html_parts.append(f"      <td>{c or ''}</td>")
                        html_parts.append("    </tr>")
                    html_parts.append("  </tbody>")
            html_parts.append("</table>")
            html_str = "\n".join(html_parts)

            content_text = "\n".join(["\t".join([str(c or "") for c in row]) for row in raw_data])

            table_elements.append(
                TableElement(
                    id=elem_id,
                    type=ElementType.TABLE,
                    page=page_number,
                    bbox=t_bbox,
                    reading_order=1,  # Will be adjusted by ReadingOrderEngine
                    content=content_text,
                    confidence=0.96,
                    provenance=SourceProvenance(
                        method=SourceMethod.GEOMETRIC_ANALYSIS,
                        engine="pymupdf_tables",
                        version=fitz.__version__,
                        confidence=0.96,
                    ),
                    rows=num_rows,
                    columns=num_cols,
                    cells=cells,
                    markdown=markdown_str,
                    html=html_str,
                )
            )

        return table_elements

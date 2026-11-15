from __future__ import annotations

import html as html_lib
from typing import Any

from trueparse.models.elements import TableCell, TableElement


class TableRenderer:
    @classmethod
    def ensure_representations(cls, table: TableElement, *, markdown: bool, html: bool) -> None:
        if markdown and table.markdown is None:
            grid = [[""] * table.columns for _ in range(table.rows)]
            for cell in table.cells:
                if cell.row < table.rows and cell.column < table.columns:
                    grid[cell.row][cell.column] = cell.text
            if not table.cells and table.content:
                grid = [line.split("\t") for line in table.content.splitlines()]
            table.markdown = cls._to_markdown(grid, table.columns)
        if html and table.html is None:
            headers = table.metadata.get(
                "header_rows", len({cell.row for cell in table.cells if cell.is_header})
            )
            table.html = cls._to_html(table.cells, table.rows, headers)

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

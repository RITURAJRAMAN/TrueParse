from __future__ import annotations

import re
from typing import Any

from trueparse.exporters.tables import TableRenderer
from trueparse.models.elements import TableElement
from trueparse.models.enums import ElementType


class TableMerger:
    @classmethod
    def merge_cross_page_tables(
        cls, pages_elements: list[list[Any]], column_tolerance_pt: float = 6.0
    ) -> list[list[Any]]:
        last_table: TableElement | None = None
        aliases: dict[str, str] = {}
        excluded = {ElementType.HEADER, ElementType.FOOTER, ElementType.PAGE_NUMBER, ElementType.CAPTION}
        for elements in pages_elements:
            tables = [element for element in elements if isinstance(element, TableElement)]
            if not tables:
                last_table = None
                continue
            for table in tables:
                position = elements.index(table)
                leading = elements[:position]
                table.metadata["continuation_hint"] = table.metadata.get("continuation_hint", False) or any(
                    re.search(r"\bcontinu(?:ed|ation)|\bcont[.'’]*d\b|carry forward", item.content, re.I)
                    for item in leading[-3:]
                )
                captions = [
                    item.content
                    for item in leading[-3:]
                    if re.match(r"^\s*(table|schedule|exhibit)\s+[A-Za-z0-9]", item.content, re.I)
                ]
                if captions:
                    table.metadata["caption_text"] = captions[-1]
                table.metadata["leading_body"] = any(
                    len(item.content.strip()) > 60
                    and item.type not in excluded
                    and not re.match(r"^\s*(table|schedule|exhibit)\b", item.content, re.I)
                    and "continu" not in item.content.lower()
                    for item in leading
                )
                table.metadata["following_body"] = any(
                    len(item.content.strip()) > 30
                    and item.type not in excluded
                    and not isinstance(item, TableElement)
                    and "continu" not in item.content.lower()
                    for item in elements[position + 1 :]
                )
            first = tables[0]
            leading = elements[: elements.index(first)]
            new_section = any(
                item.type in (ElementType.TITLE, ElementType.SECTION_HEADER)
                and "continu" not in item.content.lower()
                and not re.match(r"^\s*(table|schedule|exhibit)\b", item.content, re.I)
                for item in leading
            )
            if (
                last_table is not None
                and not new_section
                and cls._continues(last_table, first, column_tolerance_pt)
            ):
                cls._absorb(last_table, first)
                aliases[first.id] = last_table.id
                elements.remove(first)
                tables = tables[1:]
            last_table = tables[-1] if tables else last_table
        for elements in pages_elements:
            for index, element in enumerate(elements, 1):
                element.reading_order = index
                parent = element.metadata.get("parent_table_id")
                if parent in aliases:
                    element.metadata["parent_table_id"] = aliases[parent]
        return pages_elements

    @staticmethod
    def _column_origins(table: TableElement) -> list[float]:
        origins: dict[int, float] = {}
        for cell in table.cells:
            if cell.bbox is not None and cell.column not in origins:
                origins[cell.column] = cell.bbox.x0
        return [origins[k] for k in sorted(origins)]

    @classmethod
    def _continues(cls, first: TableElement, second: TableElement, tolerance: float) -> bool:
        previous_page = first.metadata.get("last_fragment_page", first.page)
        if second.page != previous_page + 1 or first.columns != second.columns:
            return False

        def normalize(value):
            return re.sub(r"\s+", " ", value.strip()).casefold()

        a_header = [(cell.row, cell.column, normalize(cell.text)) for cell in first.cells if cell.is_header]
        b_header = [(cell.row, cell.column, normalize(cell.text)) for cell in second.cells if cell.is_header]
        if a_header and b_header and a_header != b_header:
            return False
        a, b = cls._column_origins(first), cls._column_origins(second)
        if len(a) != len(b) or not a or any(abs(x - y) > tolerance for x, y in zip(a, b, strict=True)):
            return False
        for column in range(first.columns):
            left = next(
                (
                    cell.bbox
                    for cell in first.cells
                    if cell.column == column and cell.col_span == 1 and cell.bbox
                ),
                None,
            )
            right = next(
                (
                    cell.bbox
                    for cell in second.cells
                    if cell.column == column and cell.col_span == 1 and cell.bbox
                ),
                None,
            )
            if left and right and abs(left.x1 - right.x1) > tolerance:
                return False
        first_caption = first.metadata.get("caption_text", "")
        second_caption = second.metadata.get("caption_text", "")

        def caption_key(value):
            return normalize(re.sub(r"\(?\bcontinued\b\)?", "", value, flags=re.I))

        if first_caption and second_caption and caption_key(first_caption) != caption_key(second_caption):
            return False
        if first.metadata.get("following_body") or second.metadata.get("leading_body"):
            return False
        explicit = second.metadata.get("continuation_hint", False)
        same_caption = bool(first_caption and second_caption)
        previous_bbox = first.metadata.get("last_fragment_bbox") or (
            first.bbox.to_list() if first.bbox else None
        )
        height = first.metadata.get("last_fragment_height", first.metadata.get("page_height"))
        second_height = second.metadata.get("page_height")
        boundary = bool(
            previous_bbox
            and height
            and second.bbox
            and second_height
            and previous_bbox[3] >= 0.6 * height
            and second.bbox.y0 <= 0.35 * second_height
        )
        return bool(explicit or same_caption or boundary)

    @classmethod
    def _absorb(cls, target: TableElement, continuation: TableElement) -> None:
        """Appends ``continuation``'s rows onto ``target``."""
        target_headers = [(c.row, c.column, c.text.strip().casefold()) for c in target.cells if c.is_header]
        cont_headers = [
            (c.row, c.column, c.text.strip().casefold()) for c in continuation.cells if c.is_header
        ]
        repeats_header = bool(target_headers) and target_headers == cont_headers

        skip_rows = (
            max((c.row + c.row_span for c in continuation.cells if c.is_header), default=0)
            if repeats_header
            else 0
        )
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
                        "page": cell.page or continuation.page,
                    }
                )
            )

        added_rows = continuation.rows - skip_rows
        target.rows += max(0, added_rows)
        continuation_lines = continuation.content.splitlines()[skip_rows:]
        target.content = f"{target.content}\n" + "\n".join(continuation_lines)

        render_markdown = target.markdown is not None or continuation.markdown is not None
        render_html = target.html is not None or continuation.html is not None
        target.markdown = None
        target.html = None
        TableRenderer.ensure_representations(target, markdown=render_markdown, html=render_html)
        target.metadata.setdefault("continued_from_pages", []).append(continuation.page)
        target.metadata.setdefault("continuation_bboxes", []).append(
            {
                "page": continuation.page,
                "bbox": continuation.bbox.to_list() if continuation.bbox else None,
            }
        )
        target.metadata["last_fragment_page"] = continuation.page
        target.metadata["last_fragment_bbox"] = continuation.bbox.to_list() if continuation.bbox else None
        target.metadata["last_fragment_height"] = continuation.metadata.get("page_height")
        target.metadata["following_body"] = continuation.metadata.get("following_body", False)

from __future__ import annotations

import html
import re
from itertools import groupby

from trueparse.contracts.document import ListItem
from trueparse.models.elements import TableElement

_MARKDOWN = re.compile(r"([\\`*_{}\[\]<>#|!])")


def escape_markdown(text: str) -> str:
    return _MARKDOWN.sub(r"\\\1", text)


def list_markdown(items: list[ListItem], depth: int = 0) -> str:
    lines = []
    for item in items:
        marker = item.marker[:-1] + "." if item.marker and item.marker[0].isdigit() else "-"
        prefix = " " * depth + marker + " "
        text = escape_markdown(item.text).splitlines() or [""]
        lines.append(prefix + text[0])
        lines.extend(" " * len(prefix) + line for line in text[1:])
        if item.children:
            lines.append(list_markdown(item.children, depth + 4))
    return "\n".join(lines)


def list_html(items: list[ListItem]) -> str:
    parts = []
    for ordered, group in groupby(items, key=lambda item: bool(item.marker and item.marker[0].isdigit())):
        tag = "ol" if ordered else "ul"
        parts.append(f"<{tag}>")
        for item in group:
            value = f' value="{item.marker[:-1]}"' if ordered and item.marker else ""
            parts.append(f"<li{value}>{html.escape(item.text)}{list_html(item.children)}</li>")
        parts.append(f"</{tag}>")
    return "".join(parts)


def table_text(table: TableElement) -> str:
    if not table.cells:
        return table.content
    grid = [[""] * table.columns for _ in range(table.rows)]
    for cell in table.cells:
        grid[cell.row][cell.column] = cell.text.replace("\t", " ").replace("\n", " ")
    return "\n".join("\t".join(row) for row in grid)

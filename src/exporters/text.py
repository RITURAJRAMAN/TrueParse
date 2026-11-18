from __future__ import annotations

from trueparse.exporters.content import table_text
from trueparse.models.document import Document
from trueparse.models.elements import TableElement
from trueparse.models.enums import VISUAL_TYPES


class TextExporter:
    """Renders a :class:`Document` as plain UTF-8 text in reading order."""

    @classmethod
    def export(cls, document: Document, include_page_markers: bool = True) -> str:
        lines: list[str] = []

        for page in document.pages:
            if include_page_markers:
                lines.append(f"--- Page {page.page_number} ---")
                lines.append("")

            for element in page.elements:
                if element.type in VISUAL_TYPES:
                    lines.extend([f"[TrueParse visual: {element.id}]", ""])
                    continue
                content = (element.content or "").strip()
                if not content:
                    continue
                if isinstance(element, TableElement):
                    lines.append(table_text(element))
                else:
                    lines.append(content)
                lines.append("")

        return "\n".join(lines).strip() + "\n"

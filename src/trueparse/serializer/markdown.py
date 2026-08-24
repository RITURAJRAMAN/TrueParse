from __future__ import annotations
from trueparse.core.enums import ElementType
from trueparse.core.models import (
    Document,
    HeadingElement,
    TableElement,
    FigureElement,
    ChartElement,
)


class MarkdownExporter:
    """Generates clean Markdown from the canonical Document graph."""

    @classmethod
    def export(cls, document: Document) -> str:
        lines: list[str] = []

        if document.metadata.title:
            lines.append(f"# {document.metadata.title}\n")

        for page in document.pages:
            lines.append(f"\n<!-- Page {page.page_number} -->\n")

            for elem in page.elements:
                if elem.type == ElementType.HEADER or elem.type in (ElementType.FOOTER, ElementType.PAGE_NUMBER):
                    continue

                if isinstance(elem, HeadingElement) or elem.type in (ElementType.TITLE, ElementType.SECTION_HEADER):
                    level = elem.level if isinstance(elem, HeadingElement) else 2
                    prefix = "#" * max(1, min(6, level + 1))
                    lines.append(f"{prefix} {elem.content}\n")

                elif isinstance(elem, TableElement) and elem.markdown:
                    lines.append(elem.markdown + "\n")

                elif isinstance(elem, (FigureElement, ChartElement)) and elem.asset_path:
                    caption = elem.title or elem.content or "Extracted Asset"
                    lines.append(f"![{caption}]({elem.asset_path})\n")

                elif elem.type == ElementType.LIST:
                    lines.append(f"{elem.content}\n")

                elif elem.type == ElementType.CAPTION:
                    lines.append(f"*{elem.content}*\n")

                elif elem.type == ElementType.EQUATION:
                    lines.append(f"$$\n{elem.content}\n$$\n")

                else:
                    lines.append(f"{elem.content}\n")

        return "\n".join(lines).strip()

from __future__ import annotations

from trueparse.core.enums import ElementType
from trueparse.core.models import (
    ChartElement,
    Document,
    FigureElement,
    HeadingElement,
    TableElement,
)


class MarkdownExporter:
    """Generates clean Markdown from the canonical Document graph."""

    @classmethod
    def export(cls, document: Document, include_page_markers: bool = True) -> str:
        lines: list[str] = []

        if document.metadata.title:
            lines.append(f"# {document.metadata.title}\n")

        for page in document.pages:
            if include_page_markers:
                lines.append(f"\n<!-- Page {page.page_number} -->\n")

            for elem in page.elements:
                if elem.type in (
                    ElementType.HEADER, ElementType.FOOTER, ElementType.PAGE_NUMBER
                ):
                    continue

                content = (elem.content or "").strip()

                if isinstance(elem, HeadingElement) or elem.type in (
                    ElementType.TITLE, ElementType.SECTION_HEADER
                ):
                    level = getattr(elem, "level", 2)
                    prefix = "#" * max(1, min(6, level + 1))
                    lines.append(f"{prefix} {content}\n")

                elif isinstance(elem, TableElement) and elem.markdown:
                    lines.append(elem.markdown + "\n")

                elif isinstance(elem, (FigureElement, ChartElement)) and elem.asset_path:
                    caption = elem.title or content or "Extracted Asset"
                    # Markdown alt text cannot contain unescaped brackets.
                    caption = caption.replace("[", "(").replace("]", ")")
                    lines.append(f"![{caption}]({elem.asset_path})\n")

                elif elem.type == ElementType.LIST:
                    lines.append(cls._render_list(content) + "\n")

                elif elem.type == ElementType.CAPTION:
                    lines.append(f"*{content}*\n")

                elif elem.type == ElementType.EQUATION:
                    lines.append(f"$$\n{content}\n$$\n")

                elif content:
                    lines.append(f"{content}\n")

        return "\n".join(lines).strip()

    @staticmethod
    def _render_list(content: str) -> str:
        """Normalises a detected list block into Markdown bullets.

        Lines that already carry an ordered marker keep it; everything else
        becomes a ``-`` bullet so the block renders as a list rather than as a
        single run-on paragraph.
        """
        rendered: list[str] = []
        for raw in content.split("\n"):
            item = raw.strip()
            if not item:
                continue
            if item[0].isdigit() and len(item) > 1 and item[1] in ".)":
                rendered.append(item)
            else:
                rendered.append(f"- {item.lstrip('•●▪‣⁃·*-–— ').strip()}")
        return "\n".join(rendered)

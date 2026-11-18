from __future__ import annotations

from trueparse.exporters.content import escape_markdown, list_markdown
from trueparse.exporters.tables import TableRenderer
from trueparse.models.document import Document
from trueparse.models.elements import HeadingElement, TableElement
from trueparse.models.enums import VISUAL_TYPES, ElementType
from trueparse.processing.lists import parse_list


class MarkdownExporter:
    """Generates clean Markdown from the canonical Document graph."""

    @classmethod
    def export(cls, document: Document, include_page_markers: bool = True) -> str:
        lines: list[str] = []

        for page in document.pages:
            if include_page_markers:
                lines.append(f"\n<!-- Page {page.page_number} -->\n")

            for elem in page.elements:
                content = (elem.content or "").strip()
                asset_path = getattr(elem, "asset_path", None)

                if isinstance(elem, HeadingElement) or elem.type in (
                    ElementType.TITLE,
                    ElementType.SECTION_HEADER,
                ):
                    level = getattr(elem, "level", 2)
                    prefix = "#" * max(1, min(6, level))
                    lines.append(f"{prefix} {escape_markdown(content)}\n")

                elif isinstance(elem, TableElement) and elem.markdown:
                    if any(c.row_span > 1 or c.col_span > 1 for c in elem.cells):
                        TableRenderer.ensure_representations(elem, markdown=False, html=True)
                        lines.append((elem.html or "") + "\n")
                    else:
                        lines.append(elem.markdown + "\n")

                elif elem.type in VISUAL_TYPES and asset_path:
                    caption = getattr(elem, "title", None) or content or "Extracted Asset"
                    # Markdown alt text cannot contain unescaped brackets.
                    caption = caption.replace("[", "(").replace("]", ")")
                    lines.append(f"![{caption}]({asset_path})\n")

                elif elem.type == ElementType.LIST:
                    lines.append(cls._render_list(content) + "\n")

                elif elem.type == ElementType.CAPTION:
                    lines.append(f"*{escape_markdown(content)}*\n")

                elif elem.type == ElementType.EQUATION:
                    lines.append(f"{escape_markdown(content)}\n")

                elif elem.type in VISUAL_TYPES:
                    lines.append(f"[TrueParse visual: {elem.id}; image bytes omitted or unavailable]\n")

                elif content:
                    lines.append(f"{escape_markdown(content)}\n")

        return "\n".join(lines).strip()

    @staticmethod
    def _render_list(content: str) -> str:
        return list_markdown(parse_list(content))

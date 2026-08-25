"""Standalone HTML rendering of a parsed document.

The output is a single self-contained file: no external stylesheets, no
scripts, no network requests. Asset images are referenced by their relative
path inside the document's output directory, so the file works when opened
next to the ``assets/`` folder the parser produced.
"""
from __future__ import annotations

import html as html_lib

from trueparse.core.enums import ElementType
from trueparse.core.models import (
    ChartElement,
    Document,
    FigureElement,
    HeadingElement,
    TableElement,
)

_STYLE = """
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #666666;
  --rule: #e2e2e2; --accent: #0b5fff; --code-bg: #f6f6f7;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a; --fg: #e8e8ea; --muted: #9aa0a6;
    --rule: #2c3038; --accent: #7aa2ff; --code-bg: #1c1f26;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 2.5rem 1.25rem; max-width: 46rem;
  background: var(--bg); color: var(--fg);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
h1, h2, h3, h4, h5, h6 { line-height: 1.25; margin: 2rem 0 .75rem; }
h1 { font-size: 1.9rem; } h2 { font-size: 1.5rem; } h3 { font-size: 1.25rem; }
p { margin: 0 0 1rem; }
figure { margin: 1.5rem 0; }
figure img { max-width: 100%; height: auto; border-radius: 4px; }
figcaption { color: var(--muted); font-size: .875rem; margin-top: .5rem; }
.tp-table-wrap { overflow-x: auto; margin: 1.5rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { border: 1px solid var(--rule); padding: .45rem .6rem; text-align: left; vertical-align: top; }
th { background: var(--code-bg); font-weight: 600; }
.tp-equation { background: var(--code-bg); padding: .75rem 1rem; border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-x: auto; }
.tp-caption { color: var(--muted); font-style: italic; }
.tp-page-break { border: 0; border-top: 1px dashed var(--rule); margin: 2.5rem 0 1.5rem; }
.tp-page-label { color: var(--muted); font-size: .75rem; letter-spacing: .06em;
  text-transform: uppercase; margin-bottom: 1rem; }
.tp-meta { color: var(--muted); font-size: .875rem; border-bottom: 1px solid var(--rule);
  padding-bottom: 1rem; margin-bottom: 2rem; }
"""


class HTMLExporter:
    """Renders a :class:`Document` as a single self-contained HTML page."""

    @classmethod
    def export(cls, document: Document, include_page_markers: bool = True) -> str:
        title = document.metadata.title or document.source_file or document.id
        body = "\n".join(cls._body_parts(document, include_page_markers))

        return (
            "<!doctype html>\n"
            '<html lang="en">\n<head>\n'
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{html_lib.escape(title)}</title>\n"
            f"<style>{_STYLE}</style>\n"
            "</head>\n<body>\n"
            f"{body}\n"
            "</body>\n</html>\n"
        )

    @classmethod
    def _body_parts(cls, document: Document, include_page_markers: bool) -> list[str]:
        parts: list[str] = []
        meta = document.metadata

        if meta.title:
            parts.append(f"<h1>{html_lib.escape(meta.title)}</h1>")

        meta_bits = [
            f"{len(document.pages)} pages",
            f"quality {document.quality.overall_score:.2f}",
        ]
        if meta.author:
            meta_bits.insert(0, html_lib.escape(meta.author))
        parts.append(f'<div class="tp-meta">{" · ".join(meta_bits)}</div>')

        for page in document.pages:
            if include_page_markers:
                if page.page_number > 1:
                    parts.append('<hr class="tp-page-break">')
                parts.append(f'<div class="tp-page-label">Page {page.page_number}</div>')

            for element in page.elements:
                rendered = cls._render_element(element)
                if rendered:
                    parts.append(rendered)

        return parts

    @classmethod
    def _render_element(cls, element) -> str:
        if element.type in (ElementType.HEADER, ElementType.FOOTER, ElementType.PAGE_NUMBER):
            return ""

        content = (element.content or "").strip()

        if isinstance(element, HeadingElement) or element.type in (
            ElementType.TITLE, ElementType.SECTION_HEADER
        ):
            level = getattr(element, "level", 2)
            tag = f"h{max(1, min(6, level + 1))}"
            return f"<{tag}>{html_lib.escape(content)}</{tag}>"

        if isinstance(element, TableElement) and element.html:
            return f'<div class="tp-table-wrap">{element.html}</div>'

        if isinstance(element, (FigureElement, ChartElement)) and element.asset_path:
            alt = html_lib.escape(element.title or content or "Extracted asset")
            src = html_lib.escape(element.asset_path, quote=True)
            return f'<figure><img src="{src}" alt="{alt}"></figure>'

        if element.type == ElementType.EQUATION:
            return f'<div class="tp-equation">{html_lib.escape(content)}</div>'

        if element.type == ElementType.CAPTION:
            return f'<p class="tp-caption">{html_lib.escape(content)}</p>'

        if element.type == ElementType.LIST:
            items = [line.strip() for line in content.split("\n") if line.strip()]
            lis = "".join(f"<li>{html_lib.escape(item)}</li>" for item in items)
            return f"<ul>{lis}</ul>" if lis else ""

        if not content:
            return ""
        return f"<p>{html_lib.escape(content)}</p>"


class TextExporter:
    """Renders a :class:`Document` as plain UTF-8 text in reading order."""

    @classmethod
    def export(cls, document: Document, include_page_markers: bool = True) -> str:
        lines: list[str] = []

        if document.metadata.title:
            lines.extend([document.metadata.title, "=" * len(document.metadata.title), ""])

        for page in document.pages:
            if include_page_markers:
                lines.append(f"--- Page {page.page_number} ---")
                lines.append("")

            for element in page.elements:
                if element.type in (
                    ElementType.HEADER, ElementType.FOOTER, ElementType.PAGE_NUMBER
                ):
                    continue
                content = (element.content or "").strip()
                if not content:
                    continue
                if isinstance(element, TableElement) and element.markdown:
                    lines.append(element.markdown)
                else:
                    lines.append(content)
                lines.append("")

        return "\n".join(lines).strip() + "\n"

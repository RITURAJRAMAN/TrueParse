from trueparse.core.config import ParseOptions
from trueparse.core.enums import ElementType
from trueparse.core.models import (
    BoundingBox,
    Document,
    DocumentElement,
    DocumentMetadata,
    FigureElement,
    HeadingElement,
    Page,
)
from trueparse.pipeline.runner import PDFParser
from trueparse.serializer.html import HTMLExporter, TextExporter
from trueparse.serializer.markdown import MarkdownExporter


def _doc() -> Document:
    bbox = BoundingBox(x0=50.0, y0=100.0, x1=550.0, y1=140.0)
    return Document(
        id="doc_serial",
        metadata=DocumentMetadata(title="Quarterly <Report>", page_count=1),
        pages=[Page(page_number=1, width=612.0, height=792.0, elements=[
            HeadingElement(id="h1", type=ElementType.SECTION_HEADER, page=1, bbox=bbox,
                           reading_order=1, content="Results & Analysis", level=1),
            DocumentElement(id="p1", type=ElementType.PARAGRAPH, page=1, bbox=bbox,
                            reading_order=2, content="Revenue grew by 12%."),
            DocumentElement(id="l1", type=ElementType.LIST, page=1, bbox=bbox,
                            reading_order=3, content="• First point\n• Second point"),
            DocumentElement(id="f1", type=ElementType.FOOTER, page=1, bbox=bbox,
                            reading_order=4, content="Confidential"),
            FigureElement(id="fig1", type=ElementType.FIGURE, page=1, bbox=bbox,
                          reading_order=5, content="[Embedded Image]",
                          asset_id="a1", asset_path="assets/images/a1.png"),
        ])],
    )


class TestMarkdownExporter:
    def test_heading_level_maps_to_hashes(self):
        assert "## Results & Analysis" in MarkdownExporter.export(_doc())

    def test_footers_are_omitted(self):
        assert "Confidential" not in MarkdownExporter.export(_doc())

    def test_list_becomes_bullets(self):
        markdown = MarkdownExporter.export(_doc())
        assert "- First point" in markdown
        assert "- Second point" in markdown

    def test_figure_becomes_an_image_link(self):
        assert "](assets/images/a1.png)" in MarkdownExporter.export(_doc())

    def test_page_markers_can_be_disabled(self):
        assert "<!-- Page 1 -->" not in MarkdownExporter.export(_doc(), include_page_markers=False)


class TestHTMLExporter:
    def test_produces_a_standalone_document(self):
        html = HTMLExporter.export(_doc())
        assert html.startswith("<!doctype html>")
        assert "<style>" in html
        # Self-contained: no external requests.
        assert "http://" not in html and "https://" not in html

    def test_escapes_content(self):
        html = HTMLExporter.export(_doc())
        assert "Quarterly &lt;Report&gt;" in html
        assert "Results &amp; Analysis" in html

    def test_supports_both_colour_schemes(self):
        assert "prefers-color-scheme: dark" in HTMLExporter.export(_doc())

    def test_tables_scroll_rather_than_overflow(self):
        assert "overflow-x: auto" in HTMLExporter.export(_doc())

    def test_footers_are_omitted(self):
        assert "Confidential" not in HTMLExporter.export(_doc())


class TestTextExporter:
    def test_renders_plain_reading_order(self):
        text = TextExporter.export(_doc())
        assert "Results & Analysis" in text
        assert "Revenue grew by 12%." in text
        assert "Confidential" not in text

    def test_ends_with_a_newline(self):
        assert TextExporter.export(_doc()).endswith("\n")


class TestPipelineOutputs:
    def test_all_formats_are_written_when_requested(self, tmp_path, sample_pdf_path):
        options = ParseOptions(
            output_path=str(tmp_path), max_pages=2,
            emit_html=True, emit_text=True, emit_chunks=True,
        )
        doc = PDFParser(options=options).parse(sample_pdf_path)
        output = tmp_path / doc.id / "output"

        for name in ("document.json", "document.md", "document.html", "document.txt", "chunks.jsonl"):
            assert (output / name).exists(), f"{name} was not written"

    def test_optional_formats_are_skipped_by_default(self, tmp_path, sample_pdf_path):
        options = ParseOptions(output_path=str(tmp_path), max_pages=1)
        doc = PDFParser(options=options).parse(sample_pdf_path)
        output = tmp_path / doc.id / "output"

        assert (output / "document.json").exists()
        assert (output / "document.md").exists()
        for name in ("document.html", "document.txt", "chunks.jsonl"):
            assert not (output / name).exists()

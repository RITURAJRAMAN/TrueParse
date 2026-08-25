"""Reading order, heading classification, and paragraph merging."""
import pymupdf as fitz

from trueparse.core.config import ParseOptions
from trueparse.core.enums import ElementType
from trueparse.core.models import BoundingBox, DocumentElement
from trueparse.document.reading_order import ReadingOrderEngine
from trueparse.document.text_merge import dehyphenate, merge_paragraphs
from trueparse.pdf.native import NativeExtractor
from trueparse.pipeline.runner import PDFParser


def _element(elem_id: str, x0: float, y0: float, x1: float, y1: float,
             elem_type: ElementType = ElementType.PARAGRAPH, content: str = "text",
             page: int = 1) -> DocumentElement:
    return DocumentElement(
        id=elem_id,
        type=elem_type,
        page=page,
        bbox=BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1),
        reading_order=0,
        content=content,
    )


class TestReadingOrder:
    def test_single_column_is_top_to_bottom(self):
        elements = [
            _element("c", 50, 300, 550, 320),
            _element("a", 50, 100, 550, 120),
            _element("b", 50, 200, 550, 220),
        ]
        ordered = ReadingOrderEngine.order_page_elements(elements, 612.0, 792.0)
        assert [e.id for e in ordered] == ["a", "b", "c"]

    def test_two_columns_read_left_then_right(self):
        elements = [
            _element("l1", 50, 100, 280, 120),
            _element("r1", 320, 100, 550, 120),
            _element("l2", 50, 200, 280, 220),
            _element("r2", 320, 200, 550, 220),
        ]
        ordered = ReadingOrderEngine.order_page_elements(elements, 612.0, 792.0)
        assert [e.id for e in ordered] == ["l1", "l2", "r1", "r2"]

    def test_three_columns_are_discovered(self):
        """The pre-0.1.2 midline heuristic collapsed this into two columns."""
        elements = []
        for col, x0 in enumerate((50, 240, 430)):
            for row, y0 in enumerate((100, 200)):
                elements.append(_element(f"c{col}r{row}", x0, y0, x0 + 130, y0 + 20))
        ordered = ReadingOrderEngine.order_page_elements(elements, 612.0, 792.0)
        assert [e.id for e in ordered] == [
            "c0r0", "c0r1", "c1r0", "c1r1", "c2r0", "c2r1",
        ]

    def test_off_centre_columns_are_handled(self):
        """Columns need not straddle the page midline."""
        elements = [
            _element("narrow1", 50, 100, 170, 120),
            _element("narrow2", 50, 200, 170, 220),
            _element("wide1", 220, 100, 550, 120),
            _element("wide2", 220, 200, 550, 220),
        ]
        ordered = ReadingOrderEngine.order_page_elements(elements, 612.0, 792.0)
        assert [e.id for e in ordered] == ["narrow1", "narrow2", "wide1", "wide2"]

    def test_headers_first_and_footers_last(self):
        elements = [
            _element("footer", 50, 760, 550, 780, ElementType.FOOTER),
            _element("body", 50, 300, 550, 320),
            _element("header", 50, 20, 550, 40, ElementType.HEADER),
        ]
        ordered = ReadingOrderEngine.order_page_elements(elements, 612.0, 792.0)
        assert [e.id for e in ordered] == ["header", "body", "footer"]

    def test_full_width_element_breaks_columns_into_bands(self):
        elements = [
            _element("l1", 50, 100, 280, 120),
            _element("r1", 320, 100, 550, 120),
            _element("banner", 50, 150, 550, 170, ElementType.SECTION_HEADER),
            _element("l2", 50, 200, 280, 220),
            _element("r2", 320, 200, 550, 220),
        ]
        ordered = ReadingOrderEngine.order_page_elements(elements, 612.0, 792.0)
        assert [e.id for e in ordered] == ["l1", "r1", "banner", "l2", "r2"]

    def test_empty_input_returns_empty(self):
        assert ReadingOrderEngine.order_page_elements([], 612.0, 792.0) == []

    def test_reading_order_is_sequential_from_one(self):
        elements = [_element(f"e{i}", 50, i * 40.0, 550, i * 40.0 + 20) for i in range(5)]
        ordered = ReadingOrderEngine.order_page_elements(elements, 612.0, 792.0)
        assert [e.reading_order for e in ordered] == [1, 2, 3, 4, 5]


class TestThreeColumnEndToEnd:
    def test_pipeline_orders_three_columns_correctly(self, tmp_path, three_column_pdf_path):
        options = ParseOptions(output_path=str(tmp_path))
        doc = PDFParser(options=options).parse(three_column_pdf_path)

        body = [
            e.content for e in doc.pages[0].elements
            if e.type == ElementType.PARAGRAPH and "Column" in e.content
        ]
        assert body == [
            "Column 1 block 1 body text.",
            "Column 1 block 2 body text.",
            "Column 1 block 3 body text.",
            "Column 2 block 1 body text.",
            "Column 2 block 2 body text.",
            "Column 2 block 3 body text.",
            "Column 3 block 1 body text.",
            "Column 3 block 2 body text.",
            "Column 3 block 3 body text.",
        ]


class TestFontProfile:
    def test_body_size_is_character_weighted(self, sample_pdf_path):
        """Many short headings must not outvote a few dense paragraphs."""
        doc = fitz.open(sample_pdf_path)
        blocks = []
        for idx in range(len(doc)):
            page_blocks, _ = NativeExtractor.extract_page_text_blocks(doc[idx], idx + 1)
            blocks.extend(page_blocks)
        doc.close()

        profile = NativeExtractor.build_font_profile(blocks)
        assert profile.body_size == 11.0
        # 16pt and 18pt headings sit above body and form the ladder.
        assert profile.ladder
        assert max(profile.ladder) >= 16.0

    def test_empty_document_yields_a_usable_default(self):
        profile = NativeExtractor.build_font_profile([])
        assert profile.body_size > 0
        assert profile.ladder == []

    def test_level_for_returns_none_for_body_text(self):
        profile = NativeExtractor.build_font_profile([])
        assert profile.level_for(profile.body_size, is_bold=False) is None


class TestNumberedHeadings:
    def test_numbering_depth_sets_heading_level(self, tmp_path, numbered_headings_pdf_path):
        """All headings share the body font size; only numbering identifies them."""
        options = ParseOptions(output_path=str(tmp_path))
        doc = PDFParser(options=options).parse(numbered_headings_pdf_path)

        headings = {
            e.content: getattr(e, "level", None)
            for e in doc.pages[0].elements
            if e.type == ElementType.SECTION_HEADER
        }
        assert headings.get("1. Introduction") == 1
        assert headings.get("1.1 Background") == 2
        assert headings.get("1.1.1 Prior Work") == 3
        assert headings.get("2. Methodology") == 1

    def test_sections_nest_by_level_not_by_document_order(self, tmp_path, numbered_headings_pdf_path):
        """A level-2 heading following another must be a sibling, not a child."""
        options = ParseOptions(output_path=str(tmp_path))
        doc = PDFParser(options=options).parse(numbered_headings_pdf_path)

        by_title = {s.title: s for s in doc.sections}
        intro = by_title["1. Introduction"]
        background = by_title["1.1 Background"]
        prior = by_title["1.1.1 Prior Work"]
        methodology = by_title["2. Methodology"]

        assert background.parent_id == intro.id
        assert prior.parent_id == background.id
        # Both top-level sections hang off the root, not off each other.
        assert methodology.parent_id == intro.parent_id


class TestParagraphMerging:
    def test_dehyphenate_rejoins_split_words(self):
        assert dehyphenate("compre-\nhensive") == "comprehensive"

    def test_dehyphenate_preserves_intentional_hyphens(self):
        assert dehyphenate("state-of-the-art") == "state-of-the-art"
        # A capitalised tail is a real hyphenated compound, not a line break.
        assert dehyphenate("Anglo-\nSaxon") == "Anglo-\nSaxon"

    def test_open_sentence_absorbs_its_continuation(self):
        pages = [[
            _element("a", 50, 100, 280, 120, content="The quarterly results were"),
            _element("b", 320, 100, 550, 120, content="stronger than forecast."),
        ]]
        merged = merge_paragraphs(pages)
        assert len(merged[0]) == 1
        assert merged[0][0].content == "The quarterly results were stronger than forecast."
        assert merged[0][0].metadata["merged_element_ids"] == ["b"]

    def test_closed_sentence_is_left_alone(self):
        pages = [[
            _element("a", 50, 100, 550, 120, content="First complete sentence."),
            _element("b", 50, 130, 550, 150, content="Second complete sentence."),
        ]]
        merged = merge_paragraphs(pages)
        assert len(merged[0]) == 2

    def test_capitalised_continuation_is_not_absorbed(self):
        pages = [[
            _element("a", 50, 100, 550, 120, content="An open clause without a period"),
            _element("b", 50, 130, 550, 150, content="Another Heading Style Line"),
        ]]
        merged = merge_paragraphs(pages)
        assert len(merged[0]) == 2

    def test_merge_crosses_a_page_boundary(self):
        pages = [
            [_element("a", 50, 700, 550, 720, content="The sentence continues onto", page=1)],
            [_element("b", 50, 100, 550, 120, content="the following page cleanly.", page=2)],
        ]
        merged = merge_paragraphs(pages)
        assert len(merged[0]) == 1
        assert len(merged[1]) == 0
        assert merged[0][0].content == "The sentence continues onto the following page cleanly."
        assert merged[0][0].metadata["spans_pages"] == [1, 2]

    def test_tables_are_never_merged_into_prose(self):
        pages = [[
            _element("a", 50, 100, 550, 120, content="Results are shown below"),
            _element("t", 50, 130, 550, 200, ElementType.TABLE, content="a\tb"),
        ]]
        merged = merge_paragraphs(pages)
        assert len(merged[0]) == 2

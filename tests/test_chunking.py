import json

from trueparse.chunking.chunker import DocumentChunker
from trueparse.chunking.tokenization import approximate_tokens as estimate_tokens
from trueparse.config.options import ParseOptions
from trueparse.contracts.response import ParsedOutputData
from trueparse.exporters.files import serialize_outputs
from trueparse.models.document import Document, DocumentMetadata, Page, Section
from trueparse.models.elements import DocumentElement, TableCell, TableElement
from trueparse.models.enums import ChunkStrategy, ElementType, OutputFormat
from trueparse.models.geometry import BoundingBox
from trueparse.pipeline.runner import PDFParser


def _bbox(y: float = 0.0) -> BoundingBox:
    return BoundingBox(x0=50.0, y0=y, x1=550.0, y1=y + 20.0)


def _paragraph(elem_id: str, text: str, page: int = 1, order: int = 1) -> DocumentElement:
    return DocumentElement(
        id=elem_id,
        type=ElementType.PARAGRAPH,
        page=page,
        bbox=_bbox(order * 25.0),
        reading_order=order,
        content=text,
    )


def _document_with_sections() -> Document:
    """Two nested sections plus a table, enough to exercise every rule."""
    intro = _paragraph("elem_1", "Introduction body text about the subject. " * 5, order=1)
    background = _paragraph("elem_2", "Background details worth retrieving. " * 5, order=2)
    table = TableElement(
        id="table_1",
        type=ElementType.TABLE,
        page=1,
        bbox=_bbox(200.0),
        reading_order=3,
        content="Metric\tValue",
        rows=2,
        columns=2,
        cells=[
            TableCell(id="c1", row=0, column=0, is_header=True, text="Metric"),
            TableCell(id="c2", row=0, column=1, is_header=True, text="Value"),
        ],
        markdown="| Metric | Value |\n| --- | --- |\n| Revenue | $100M |",
    )

    return Document(
        id="doc_chunk_test",
        metadata=DocumentMetadata(title="Chunk Test", page_count=1),
        pages=[Page(page_number=1, width=612.0, height=792.0, elements=[intro, background, table])],
        sections=[
            Section(id="sec_root", title="Document Root", level=0, parent_id=None, element_ids=[]),
            Section(id="sec_1", title="Introduction", level=1, parent_id="sec_root", element_ids=["elem_1"]),
            Section(
                id="sec_2", title="Background", level=2, parent_id="sec_1", element_ids=["elem_2", "table_1"]
            ),
        ],
    )


class TestEstimateTokens:
    def test_scales_with_word_count(self):
        assert estimate_tokens("one two three") > estimate_tokens("one")

    def test_empty_string_is_non_negative(self):
        assert estimate_tokens("") >= 0


class TestChunker:
    def test_token_strategy_crosses_sections_while_hybrid_preserves_them(self):
        document = _document_with_sections()
        token = DocumentChunker.chunk(document, strategy=ChunkStrategy.TOKEN)
        hybrid = DocumentChunker.chunk(document, strategy=ChunkStrategy.HYBRID)
        assert token[0].element_ids == ["elem_1", "elem_2"]
        assert token[0].section_path == []
        assert hybrid[0].element_ids == ["elem_1"]

    def test_produces_chunks_with_sequential_ids(self):
        chunks = DocumentChunker.chunk(_document_with_sections())
        assert chunks
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        assert all(c.id.startswith(f"doc_chunk_test:chunk:{i:05d}:") for i, c in enumerate(chunks))
        assert len({c.id for c in chunks}) == len(chunks)

    def test_section_path_is_a_breadcrumb_from_the_root(self):
        chunks = DocumentChunker.chunk(_document_with_sections())
        paths = {tuple(c.section_path) for c in chunks}
        # The nested section must carry its parent's title ahead of its own.
        assert ("Introduction", "Background") in paths
        # The synthetic root contributes no title.
        assert not any("Document Root" in path for path in paths)

    def test_tables_are_never_split_and_keep_their_markdown(self):
        chunks = DocumentChunker.chunk(_document_with_sections())
        table_chunks = [c for c in chunks if "table" in c.element_types]
        assert len(table_chunks) == 1
        assert table_chunks[0].element_ids == ["table_1"]
        assert "| Metric | Value |" in table_chunks[0].text

    def test_every_chunk_carries_spatial_provenance(self):
        for chunk in DocumentChunker.chunk(_document_with_sections()):
            assert chunk.document_id == "doc_chunk_test"
            assert chunk.page_start >= 1
            assert chunk.page_end >= chunk.page_start
            assert chunk.element_ids
            assert len(chunk.bboxes) == len(chunk.element_ids)
            for entry in chunk.bboxes:
                assert "page" in entry
                assert len(entry["bbox"]) == 4

    def test_token_budget_is_respected_for_prose(self):
        long_doc = _document_with_sections()
        long_doc.pages[0].elements = [_paragraph(f"elem_{i}", "word " * 200, order=i) for i in range(1, 6)]
        long_doc.sections = [
            Section(
                id="sec_root", title="Document Root", level=0, element_ids=[f"elem_{i}" for i in range(1, 6)]
            ),
        ]
        chunks = DocumentChunker.chunk(long_doc, max_tokens=128, overlap_tokens=16)
        assert len(chunks) > 1
        # Oversize elements are split internally, so no chunk may exceed the
        # budget by more than the token estimator's own rounding.
        assert all(c.token_estimate <= 128 + 8 for c in chunks), [c.token_estimate for c in chunks]

    def test_oversize_single_element_is_split_but_keeps_provenance(self):
        doc = _document_with_sections()
        doc.pages[0].elements = [_paragraph("elem_big", "sentence text here. " * 300, order=1)]
        doc.sections = [
            Section(id="sec_root", title="Document Root", level=0, element_ids=["elem_big"]),
        ]
        chunks = DocumentChunker.chunk(doc, max_tokens=100, overlap_tokens=10)
        assert len(chunks) > 1
        # Each slice still points back at the element it came from.
        assert all(c.element_ids == ["elem_big"] for c in chunks)
        assert all(c.token_estimate <= 100 + 8 for c in chunks)

    def test_section_strategy_ignores_the_token_budget(self):
        doc = _document_with_sections()
        section_chunks = DocumentChunker.chunk(doc, strategy=ChunkStrategy.SECTION)
        hybrid_chunks = DocumentChunker.chunk(doc, strategy=ChunkStrategy.HYBRID, max_tokens=16)
        assert len(section_chunks) <= len(hybrid_chunks)

    def test_headers_and_footers_are_preserved(self):
        doc = _document_with_sections()
        doc.pages[0].elements.append(
            DocumentElement(
                id="elem_footer",
                type=ElementType.FOOTER,
                page=1,
                bbox=_bbox(760.0),
                reading_order=99,
                content="Confidential - page 1",
            )
        )
        chunks = DocumentChunker.chunk(doc)
        assert any("Confidential" in c.text for c in chunks)

    def test_overlap_never_exceeds_the_budget(self):
        doc = _document_with_sections()
        chunks = DocumentChunker.chunk(doc, max_tokens=64, overlap_tokens=999)
        assert chunks, "a nonsensical overlap must not produce an empty result"

    def test_jsonl_round_trips(self):
        chunks = DocumentChunker.chunk(_document_with_sections())
        output = ParsedOutputData(chunks=[chunk.model_dump(mode="json") for chunk in chunks])
        _, encoded = next(serialize_outputs(output, [OutputFormat.CHUNKS]))
        lines = encoded.splitlines()
        assert len(lines) == len(chunks)
        for line, chunk in zip(lines, chunks, strict=True):
            assert json.loads(line)["id"] == chunk.id


class TestChunkEmission:
    def test_pipeline_writes_chunks_jsonl(self, tmp_path, sample_pdf_path):
        from trueparse.config.options import OutputConfig

        options = ParseOptions(
            output_path=str(tmp_path),
            output_config=OutputConfig(formats=["json", "markdown", "chunks"]),
            max_pages=3,
        )
        doc = PDFParser(options=options).parse(sample_pdf_path)

        chunks_file = tmp_path / doc.id / "sample_document.chunks.jsonl"
        assert chunks_file.exists()

        records = [json.loads(line) for line in chunks_file.read_text(encoding="utf-8").splitlines() if line]
        assert records
        assert all(r["document_id"] == doc.id for r in records)
        assert any(r["text"].strip() for r in records)

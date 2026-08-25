import pytest

from trueparse.core.config import ParseOptions
from trueparse.pdf.inspector import PDFInspector
from trueparse.pipeline.runner import PDFParser


def test_pdf_inspector(sample_pdf_path):
    assert sample_pdf_path.exists(), f"Expected test PDF at {sample_pdf_path}"
    inspection = PDFInspector.inspect(sample_pdf_path)
    assert inspection.page_count > 0
    assert len(inspection.sha256) == 64
    assert inspection.pages[0].width > 0
    assert inspection.pages[0].height > 0


def test_end_to_end_pipeline(tmp_path, sample_pdf_path):
    output_dir = tmp_path / "test_output"
    options = ParseOptions(
        output_path=str(output_dir),
        debug=True,
        max_pages=3,
    )
    parser = PDFParser(options=options)
    doc = parser.parse(sample_pdf_path)

    assert doc.id.startswith("doc_")
    assert doc.source_file == sample_pdf_path.name
    assert len(doc.pages) > 0
    assert doc.metadata.page_count > 0

    doc_dir = output_dir / doc.id
    doc_json = doc_dir / "output" / "document.json"
    doc_md = doc_dir / "output" / "document.md"
    assert doc_json.exists()
    assert doc_md.exists()

    # Check debug renders exist because debug=True
    debug_dir = doc_dir / "debug" / "pages"
    assert debug_dir.exists()
    page_pngs = list(debug_dir.glob("*.png"))
    assert len(page_pngs) == len(doc.pages)

    # Check assets directory
    assets_dir = doc_dir / "assets"
    assert assets_dir.exists()

    # Check source PDF is saved with original filename
    source_file = doc_dir / "source" / sample_pdf_path.name
    assert source_file.exists()


def test_vector_figures_are_deduplicated_by_hash(tmp_path):
    """The same vector graphic repeated across pages must be stored once."""
    import pymupdf as fitz

    pdf_path = tmp_path / "repeated_logo.pdf"
    doc = fitz.open()
    for _ in range(4):
        page = doc.new_page(width=612, height=792)
        # An identical vector shape on every page.
        page.draw_rect(fitz.Rect(60, 60, 180, 180), color=(0, 0, 1), fill=(0.2, 0.4, 0.9))
        page.draw_line(fitz.Point(60, 60), fitz.Point(180, 180))
        page.insert_text((60, 300), "Body text for this page.", fontsize=11)
    doc.save(str(pdf_path))
    doc.close()

    options = ParseOptions(output_path=str(tmp_path / "out"), extract_charts=True)
    parsed = PDFParser(options=options).parse(pdf_path)

    figures = [a for a in parsed.assets.values() if a.type.value == "figure"]
    assert figures, "expected at least one vector figure asset"
    # One asset, four recorded occurrences - not four assets.
    assert len(figures) == 1
    assert len(figures[0].occurrences) == 4
    assert figures[0].sha256, "vector crops must be hashed for deduplication"

    figures_dir = tmp_path / "out" / parsed.id / "assets" / "figures"
    assert len(list(figures_dir.glob("*.png"))) == 1


def test_all_assets_have_a_sha256(tmp_path, sample_pdf_path):
    options = ParseOptions(output_path=str(tmp_path), max_pages=3)
    parsed = PDFParser(options=options).parse(sample_pdf_path)
    for asset in parsed.assets.values():
        assert asset.sha256, f"asset {asset.id} is missing its hash"


def test_encrypted_pdf_roundtrip(tmp_path, encrypted_pdf_path):
    options = ParseOptions(output_path=str(tmp_path), password="s3cret")
    parsed = PDFParser(options=options).parse(encrypted_pdf_path)
    assert len(parsed.pages) == 1
    text = " ".join(e.content for e in parsed.pages[0].elements)
    assert "Confidential Quarterly Report" in text


def test_encrypted_pdf_without_password_raises(tmp_path, encrypted_pdf_path):
    from trueparse.core.enums import ErrorCode
    from trueparse.core.errors import PDFEngineError

    options = ParseOptions(output_path=str(tmp_path))
    with pytest.raises(PDFEngineError) as exc:
        PDFParser(options=options).parse(encrypted_pdf_path)
    assert exc.value.code == ErrorCode.PDF_PASSWORD_REQUIRED


def test_wrong_password_is_reported_distinctly(tmp_path, encrypted_pdf_path):
    from trueparse.core.enums import ErrorCode
    from trueparse.core.errors import PDFEngineError

    options = ParseOptions(output_path=str(tmp_path), password="wrong")
    with pytest.raises(PDFEngineError) as exc:
        PDFParser(options=options).parse(encrypted_pdf_path)
    assert exc.value.code == ErrorCode.PDF_PASSWORD_INCORRECT


def test_schema_version_is_current(tmp_path, sample_pdf_path):
    options = ParseOptions(output_path=str(tmp_path), max_pages=1)
    parsed = PDFParser(options=options).parse(sample_pdf_path)
    assert parsed.schema_version == "1.1"
    assert parsed.engine_version

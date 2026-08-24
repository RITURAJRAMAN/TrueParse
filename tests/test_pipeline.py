import os
import shutil
from pathlib import Path
import pytest
from trueparse.pipeline.runner import PDFParser
from trueparse.core.config import ParseOptions
from trueparse.pdf.inspector import PDFInspector


DATA_DIR = Path(__file__).parent.parent / "Data" / "InputPDF"
TEST_PDF = DATA_DIR / "Q226+Mgt+Report.pdf"


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
        max_pages=3,  # parse first 3 pages for rapid test
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
    assert len(page_pngs) == 3

    # Check assets directory
    assets_dir = doc_dir / "assets"
    assert assets_dir.exists()

    # Check source PDF is saved with original filename
    source_file = doc_dir / "source" / TEST_PDF.name
    assert source_file.exists()

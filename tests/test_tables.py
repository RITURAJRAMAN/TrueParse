from pathlib import Path
import pymupdf as fitz
from trueparse.tables.native import NativeTableExtractor
from trueparse.core.enums import ElementType

DATA_DIR = Path(__file__).parent.parent / "Data" / "InputPDF"
TEST_PDF = DATA_DIR / "Q226+Mgt+Report.pdf"


def test_native_table_extraction(sample_pdf_path):
    doc = fitz.open(sample_pdf_path)
    tables_found = 0
    for page_idx in range(len(doc)):
        tables = NativeTableExtractor.extract_page_tables(doc[page_idx], page_idx + 1)
        for t in tables:
            tables_found += 1
            assert t.type == ElementType.TABLE
            assert t.rows > 0
            assert t.columns > 0
            assert len(t.cells) == t.rows * t.columns
            assert t.markdown is not None
            assert t.html is not None
            assert "<table>" in t.html
    doc.close()

import pytest
from pathlib import Path
import pymupdf as fitz


@pytest.fixture(scope="session")
def sample_pdf_path(tmp_path_factory) -> Path:
    """Provides a guaranteed valid PDF for testing across all platforms and clean clones."""
    data_pdf = Path(__file__).parent.parent / "Data" / "InputPDF" / "Q226+Mgt+Report.pdf"
    if data_pdf.exists():
        return data_pdf

    # Generate synthetic multi-page document with text, table, and image if data folder is missing
    fixture_dir = tmp_path_factory.mktemp("fixtures")
    pdf_path = fixture_dir / "test_sample.pdf"

    doc = fitz.open()
    # Page 1: Text and Heading
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((54, 72), "Executive Summary", fontsize=18)
    p1.insert_text((54, 120), "This is a test paragraph describing performance metrics.", fontsize=11)
    
    # Page 2: Table
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((54, 72), "Financial Table", fontsize=16)
    # Draw simple table grid
    p2.draw_rect(fitz.Rect(54, 100, 500, 200))
    p2.draw_line(fitz.Point(54, 130), fitz.Point(500, 130))
    p2.draw_line(fitz.Point(250, 100), fitz.Point(250, 200))
    p2.insert_text((60, 120), "Metric", fontsize=11)
    p2.insert_text((260, 120), "Value", fontsize=11)
    p2.insert_text((60, 160), "Revenue", fontsize=11)
    p2.insert_text((260, 160), "$100M", fontsize=11)

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path

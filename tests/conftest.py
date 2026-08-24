import io
import pytest
from pathlib import Path
import pymupdf as fitz
from PIL import Image, ImageDraw


@pytest.fixture(scope="session")
def sample_pdf_path(tmp_path_factory) -> Path:
    """Provides a guaranteed valid, self-contained multi-page PDF for testing across all platforms and CI runners."""
    fixture_dir = tmp_path_factory.mktemp("fixtures")
    pdf_path = fixture_dir / "sample_document.pdf"

    doc = fitz.open()

    # Generate synthetic image
    img = Image.new("RGB", (200, 100), color=(73, 109, 137))
    d = ImageDraw.Draw(img)
    d.text((10, 40), "TrueParse Asset", fill=(255, 255, 0))
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="PNG")
    img_bytes = img_byte_arr.getvalue()

    # Page 1: Headings, text, and embedded image
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((54, 72), "Executive Summary", fontsize=18)
    p1.insert_text((54, 120), "This is a test paragraph describing TrueParse performance metrics.", fontsize=11)
    p1.insert_image(fitz.Rect(54, 160, 254, 260), stream=img_bytes)

    # Page 2: Ruled Table
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((54, 72), "Financial Table", fontsize=16)
    # Draw table border and lines
    p2.draw_rect(fitz.Rect(54, 100, 500, 220))
    p2.draw_line(fitz.Point(54, 140), fitz.Point(500, 140))
    p2.draw_line(fitz.Point(54, 180), fitz.Point(500, 180))
    p2.draw_line(fitz.Point(250, 100), fitz.Point(250, 220))
    # Header cells
    p2.insert_text((60, 125), "Metric", fontsize=11)
    p2.insert_text((260, 125), "Value", fontsize=11)
    # Row 1
    p2.insert_text((60, 165), "Revenue", fontsize=11)
    p2.insert_text((260, 165), "$100M", fontsize=11)
    # Row 2
    p2.insert_text((60, 205), "Net Margin", fontsize=11)
    p2.insert_text((260, 205), "24.5%", fontsize=11)

    # Page 3: Concluding Section
    p3 = doc.new_page(width=612, height=792)
    p3.insert_text((54, 72), "Conclusion & Recommendations", fontsize=16)
    p3.insert_text((54, 120), "TrueParse local engine provides deterministic document intelligence.", fontsize=11)

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path

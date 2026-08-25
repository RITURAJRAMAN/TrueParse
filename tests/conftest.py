import io
import os
import tempfile
from pathlib import Path

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

# The API reads its output root and worker mode from the environment at import
# time, so both must be set before any test imports trueparse.api.routes.
_TEST_OUTPUT_ROOT = Path(tempfile.mkdtemp(prefix="trueparse_tests_"))
os.environ.setdefault("TRUEPARSE_OUTPUT_ROOT", str(_TEST_OUTPUT_ROOT))
# Threads keep the suite fast and deterministic; the process pool is exercised
# by its own dedicated test.
os.environ.setdefault("TRUEPARSE_WORKER_MODE", "thread")


@pytest.fixture(scope="session")
def test_output_root() -> Path:
    """The directory the API server is configured to write into."""
    return _TEST_OUTPUT_ROOT


@pytest.fixture(scope="session")
def sample_pdf_path(tmp_path_factory) -> Path:
    """A valid, self-contained multi-page PDF used across the suite."""
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
    p2.draw_rect(fitz.Rect(54, 100, 500, 220))
    p2.draw_line(fitz.Point(54, 140), fitz.Point(500, 140))
    p2.draw_line(fitz.Point(54, 180), fitz.Point(500, 180))
    p2.draw_line(fitz.Point(250, 100), fitz.Point(250, 220))
    p2.insert_text((60, 125), "Metric", fontsize=11)
    p2.insert_text((260, 125), "Value", fontsize=11)
    p2.insert_text((60, 165), "Revenue", fontsize=11)
    p2.insert_text((260, 165), "$100M", fontsize=11)
    p2.insert_text((60, 205), "Net Margin", fontsize=11)
    p2.insert_text((260, 205), "24.5%", fontsize=11)

    # Page 3: Concluding Section
    p3 = doc.new_page(width=612, height=792)
    p3.insert_text((54, 72), "Conclusion & Recommendations", fontsize=16)
    p3.insert_text((54, 120), "TrueParse local engine provides deterministic document intelligence.", fontsize=11)

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture(scope="session")
def three_column_pdf_path(tmp_path_factory) -> Path:
    """A three-column layout, which the pre-0.1.2 midline heuristic mis-ordered."""
    pdf_path = tmp_path_factory.mktemp("fixtures_cols") / "three_column.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    page.insert_text((54, 60), "Three Column Report", fontsize=20)

    # Columns at x = 54, 240, 426; each 150pt wide with a 36pt gutter.
    for col_idx, x in enumerate((54, 240, 426)):
        for row_idx, y in enumerate((120, 200, 280)):
            page.insert_text(
                (x, y),
                f"Column {col_idx + 1} block {row_idx + 1} body text.",
                fontsize=10,
            )

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture(scope="session")
def spanned_table_pdf_path(tmp_path_factory) -> Path:
    """A ruled table whose first row is a single cell spanning both columns."""
    pdf_path = tmp_path_factory.mktemp("fixtures_span") / "spanned_table.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    left, right, top = 54.0, 500.0, 100.0
    row_h = 40.0
    # Outer border and three horizontal rules => 3 rows.
    page.draw_rect(fitz.Rect(left, top, right, top + row_h * 3))
    page.draw_line(fitz.Point(left, top + row_h), fitz.Point(right, top + row_h))
    page.draw_line(fitz.Point(left, top + row_h * 2), fitz.Point(right, top + row_h * 2))
    # Vertical divider only below the first row, making row 0 a 2-column span.
    page.draw_line(
        fitz.Point(277, top + row_h), fitz.Point(277, top + row_h * 3)
    )

    page.insert_text((60, top + 25), "Consolidated Results", fontsize=11)
    page.insert_text((60, top + row_h + 25), "Metric", fontsize=11)
    page.insert_text((285, top + row_h + 25), "Value", fontsize=11)
    page.insert_text((60, top + row_h * 2 + 25), "Revenue", fontsize=11)
    page.insert_text((285, top + row_h * 2 + 25), "$100M", fontsize=11)

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture(scope="session")
def numbered_headings_pdf_path(tmp_path_factory) -> Path:
    """Headings distinguished only by numbering, all at body font size.

    Font-ratio detection alone cannot classify these, so this exercises the
    numbering-pattern path.
    """
    pdf_path = tmp_path_factory.mktemp("fixtures_numbered") / "numbered.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    y = 80
    entries = [
        "1. Introduction",
        "Body text under the introduction section that is long enough to be prose.",
        "1.1 Background",
        "More body text describing the background of this particular study in detail.",
        "1.1.1 Prior Work",
        "Further body text discussing prior work and its relationship to this study.",
        "2. Methodology",
        "Body text describing the methodology applied throughout the experiments.",
    ]
    for entry in entries:
        page.insert_text((54, y), entry, fontsize=11)
        y += 40

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture(scope="session")
def scanned_pdf_path(tmp_path_factory) -> Path:
    """An image-only page with no extractable text layer."""
    pdf_path = tmp_path_factory.mktemp("fixtures_scan") / "scanned.pdf"

    try:
        font = ImageFont.load_default(size=64)
    except TypeError:  # Pillow < 10.1 has no size argument
        font = ImageFont.load_default()

    img = Image.new("RGB", (1600, 300), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((60, 110), "SCANNED INVOICE TOTAL 1234", fill=(0, 0, 0), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(20, 20, 592, 130), stream=buf.getvalue())
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture(scope="session")
def encrypted_pdf_path(tmp_path_factory) -> Path:
    """A password-protected PDF (user password: ``s3cret``)."""
    pdf_path = tmp_path_factory.mktemp("fixtures_enc") / "encrypted.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((54, 72), "Confidential Quarterly Report", fontsize=16)
    page.insert_text((54, 120), "This document is protected by a user password.", fontsize=11)
    doc.save(
        str(pdf_path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="s3cret",
        owner_pw="s3cret",
    )
    doc.close()
    return pdf_path

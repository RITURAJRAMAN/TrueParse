from trueparse.core.enums import AssetType, ElementType, RelationshipType, SourceMethod
from trueparse.core.models import (
    Asset,
    AssetOccurrence,
    BoundingBox,
    Document,
    DocumentMetadata,
    HeadingElement,
    Page,
    Relationship,
    SourceProvenance,
    TableCell,
    TableElement,
)
from trueparse.serializer.json import JSONSerializer


def test_model_roundtrip_serialization():
    bbox = BoundingBox(x0=50.0, y0=100.0, x1=300.0, y1=200.0)
    assert bbox.width == 250.0
    assert bbox.height == 100.0
    assert bbox.area == 25000.0

    provenance = SourceProvenance(
        method=SourceMethod.NATIVE_PDF,
        engine="pymupdf",
        version="1.23.0",
        confidence=0.99,
    )

    elem1 = HeadingElement(
        id="elem_01",
        type=ElementType.SECTION_HEADER,
        page=1,
        bbox=bbox,
        reading_order=1,
        content="Executive Summary",
        level=1,
        provenance=provenance,
    )

    cell1 = TableCell(
        id="cell_01",
        row=0,
        column=0,
        is_header=True,
        text="Metric",
    )
    table_elem = TableElement(
        id="table_01",
        type=ElementType.TABLE,
        page=1,
        bbox=bbox,
        reading_order=2,
        content="Metric\nRevenue",
        rows=1,
        columns=1,
        cells=[cell1],
        markdown="| Metric |\n| --- |",
    )

    asset = Asset(
        id="img_123",
        type=AssetType.IMAGE,
        path="assets/images/img_123.png",
        mime_type="image/png",
        sha256="abcdef123456",
        width=400,
        height=300,
        occurrences=[AssetOccurrence(page=1, bbox=bbox)],
        source=provenance,
    )

    rel = Relationship(
        id="rel_01",
        type=RelationshipType.CONTAINS,
        source_id="sec_01",
        target_id="elem_01",
    )

    doc = Document(
        id="doc_test",
        metadata=DocumentMetadata(
            title="Sample Report",
            page_count=1,
            file_size_bytes=1024,
            sha256="abc123hash",
        ),
        pages=[
            Page(
                page_number=1,
                width=612.0,
                height=792.0,
                elements=[elem1, table_elem],
                asset_ids=["img_123"],
            )
        ],
        assets={"img_123": asset},
        relationships=[rel],
    )

    # Test JSON serialization and deserialization
    json_str = JSONSerializer.serialize(doc)
    deserialized = JSONSerializer.deserialize(json_str)

    assert deserialized.id == "doc_test"
    assert deserialized.metadata.title == "Sample Report"
    assert len(deserialized.pages) == 1
    assert len(deserialized.pages[0].elements) == 2
    assert deserialized.assets["img_123"].width == 400


def test_version_matches_pyproject():
    """Guards against pyproject, the metadata fallback, and the API drifting apart.

    Comparing get_version() to itself passes even when the installed metadata is
    stale, so the declared version is read straight from pyproject.toml.
    """
    import re
    from pathlib import Path

    from trueparse.core.version import _FALLBACK_VERSION, get_version

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    assert declared, "version not found in pyproject.toml"

    assert get_version() == declared.group(1)
    assert _FALLBACK_VERSION == declared.group(1)


def test_engine_version_is_stamped_into_output(tmp_path, sample_pdf_path):
    from trueparse.core.config import ParseOptions
    from trueparse.core.version import get_version
    from trueparse.pipeline.runner import PDFParser

    doc = PDFParser(options=ParseOptions(output_path=str(tmp_path), max_pages=1)).parse(
        sample_pdf_path
    )
    assert doc.engine_version == get_version()

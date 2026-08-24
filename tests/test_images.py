import pymupdf as fitz
from trueparse.pdf.images import ImageExtractor
from trueparse.core.enums import AssetType


def test_embedded_image_extraction_and_deduplication(sample_pdf_path):
    doc = fitz.open(sample_pdf_path)
    assets_map = ImageExtractor.extract_embedded_images(doc)

    assert len(assets_map) > 0, "Expected at least 1 extracted image from sample_pdf_path"

    # Check that every extracted asset has valid image bytes and valid SHA-256
    seen_hashes = set()
    for asset_id, (extracted, asset_model) in assets_map.items():
        assert extracted.image_bytes is not None
        assert len(extracted.image_bytes) > 0
        assert extracted.sha256 not in seen_hashes  # Must be strictly deduplicated
        seen_hashes.add(extracted.sha256)
        assert asset_model.type == AssetType.IMAGE
        assert asset_model.source.method.value == "embedded_pdf_image"
        assert len(asset_model.occurrences) > 0

    doc.close()

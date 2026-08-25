from __future__ import annotations

import hashlib

import pymupdf as fitz  # PyMuPDF

from trueparse.core.enums import AssetType, SourceMethod
from trueparse.core.models import Asset, AssetOccurrence, BoundingBox, SourceProvenance


class ExtractedImageAsset:
    def __init__(
        self,
        asset_id: str,
        sha256: str,
        image_bytes: bytes,
        ext: str,
        mime_type: str,
        width: int,
        height: int,
        occurrences: list[AssetOccurrence],
    ):
        self.asset_id = asset_id
        self.sha256 = sha256
        self.image_bytes = image_bytes
        self.ext = ext
        self.mime_type = mime_type
        self.width = width
        self.height = height
        self.occurrences = occurrences


class ImageExtractor:
    """Extracts actual embedded raster images from PDF pages and deduplicates them by SHA-256."""

    @classmethod
    def extract_embedded_images(
        cls,
        doc: fitz.Document,
        relative_asset_dir: str = "assets/images"
    ) -> dict[str, tuple[ExtractedImageAsset, Asset]]:
        """
        Returns a mapping of asset_id -> (ExtractedImageAsset, Asset model).
        Ensures SHA-256 deduplication: identical images share a single Asset and ExtractedImageAsset.
        """
        # Map sha256 -> ExtractedImageAsset
        hash_to_extracted: dict[str, ExtractedImageAsset] = {}
        # Map xref -> sha256 to avoid re-extracting same xref
        xref_to_hash: dict[int, str] = {}

        for page_idx in range(len(doc)):
            page_num = page_idx + 1
            page = doc[page_idx]
            image_list = page.get_images(full=True) or []

            for img_info in image_list:
                xref = img_info[0]
                if xref <= 0:
                    continue

                if xref in xref_to_hash:
                    sha256 = xref_to_hash[xref]
                    extracted = hash_to_extracted[sha256]
                else:
                    try:
                        base_image = doc.extract_image(xref)
                    except Exception:
                        continue

                    if not base_image or not base_image.get("image"):
                        continue

                    img_bytes = base_image["image"]
                    ext = base_image.get("ext", "png").lower()
                    width = base_image.get("width", 0)
                    height = base_image.get("height", 0)

                    # Normalize mime type
                    mime_type = f"image/{ext}" if ext != "jpg" else "image/jpeg"

                    sha256 = hashlib.sha256(img_bytes).hexdigest()
                    xref_to_hash[xref] = sha256

                    if sha256 in hash_to_extracted:
                        extracted = hash_to_extracted[sha256]
                    else:
                        img_counter = len(hash_to_extracted) + 1
                        asset_id = f"asset_img_{img_counter:04d}"
                        extracted = ExtractedImageAsset(
                            asset_id=asset_id,
                            sha256=sha256,
                            image_bytes=img_bytes,
                            ext=ext,
                            mime_type=mime_type,
                            width=width,
                            height=height,
                            occurrences=[],
                        )
                        hash_to_extracted[sha256] = extracted

                # Get rects on current page
                rects = page.get_image_rects(xref)
                if rects:
                    for r in rects:
                        bbox = BoundingBox.from_rect((r.x0, r.y0, r.x1, r.y1))
                        # Check if this occurrence already exists
                        if not any(
                            occ.page == page_num and
                            abs(occ.bbox.x0 - bbox.x0) < 1e-2 and
                            abs(occ.bbox.y0 - bbox.y0) < 1e-2
                            for occ in extracted.occurrences
                        ):
                            extracted.occurrences.append(
                                AssetOccurrence(page=page_num, bbox=bbox)
                            )
                else:
                    # Default bbox if not resolvable
                    extracted.occurrences.append(
                        AssetOccurrence(
                            page=page_num,
                            bbox=BoundingBox(x0=0.0, y0=0.0, x1=float(extracted.width), y1=float(extracted.height))
                        )
                    )

        # Build public Asset models
        result: dict[str, tuple[ExtractedImageAsset, Asset]] = {}
        for extracted in hash_to_extracted.values():
            path_str = f"{relative_asset_dir}/{extracted.asset_id}.{extracted.ext}"
            asset_model = Asset(
                id=extracted.asset_id,
                type=AssetType.IMAGE,
                path=path_str,
                mime_type=extracted.mime_type,
                sha256=extracted.sha256,
                width=extracted.width,
                height=extracted.height,
                occurrences=extracted.occurrences,
                source=SourceProvenance(
                    method=SourceMethod.EMBEDDED_PDF_IMAGE,
                    engine="pymupdf",
                    version=fitz.__version__,
                    confidence=1.0,
                ),
            )
            result[extracted.asset_id] = (extracted, asset_model)

        return result

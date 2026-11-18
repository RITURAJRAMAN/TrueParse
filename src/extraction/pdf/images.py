from __future__ import annotations

import hashlib
from collections.abc import Callable

import pymupdf as fitz  # PyMuPDF

from trueparse.exceptions import PDFEngineError
from trueparse.models.assets import Asset, AssetOccurrence
from trueparse.models.elements import SourceProvenance
from trueparse.models.enums import AssetType, ErrorCode, SourceMethod
from trueparse.models.geometry import BoundingBox


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
    @classmethod
    def extract_embedded_images(
        cls,
        doc: fitz.Document,
        relative_asset_dir: str = "assets/images",
        max_pages: int | None = None,
        check_deadline: Callable[[], None] | None = None,
        warnings: list[str] | None = None,
        include_bytes: bool = True,
        max_asset_bytes: int = 64 * 1024 * 1024,
        max_image_pixels: int = 25_000_000,
    ) -> dict[str, tuple[ExtractedImageAsset, Asset]]:
        resources: dict[int, bytes] = {}
        by_digest: dict[bytes, int] = {}
        assets: dict[str, tuple[ExtractedImageAsset, Asset]] = {}
        identity_to_asset: dict[object, str] = {}
        occurrences: dict[str, set[tuple]] = {}
        total_bytes = 0
        for page_index in range(min(len(doc), max_pages or len(doc))):
            if check_deadline:
                check_deadline()
            page_number = page_index + 1
            try:
                page = doc[page_index]
                visible = page.get_image_info()
                if not visible:
                    continue
                if any(item["width"] * item["height"] > max_image_pixels for item in visible):
                    raise PDFEngineError(
                        ErrorCode.PDF_RESOURCE_LIMIT, "Embedded image exceeds the pixel budget."
                    )
                for image in page.get_images(full=True):
                    xref = image[0]
                    if xref <= 0 or xref in resources:
                        continue
                    if image[2] * image[3] > max_image_pixels:
                        continue
                    pixmap = fitz.Pixmap(doc, xref)
                    digest = pixmap.digest
                    del pixmap
                    resources[xref] = digest
                    by_digest.setdefault(digest, xref)
                visible = page.get_image_info(hashes=True)
            except PDFEngineError:
                raise
            except Exception as exc:
                if warnings is not None:
                    warnings.append(f"Page {page_number} image inspection failed: {exc}")
                continue
            inline_blocks = None
            for item in visible:
                digest = item["digest"]
                xref = by_digest.get(digest)
                identity = xref if xref is not None else digest
                asset_id = identity_to_asset.get(identity)
                if asset_id is None:
                    data, extension, mime_type = b"", "bin", "application/octet-stream"
                    if include_bytes:
                        try:
                            if xref is not None:
                                extracted = doc.extract_image(xref) or {}
                            else:
                                if inline_blocks is None:
                                    inline_blocks = page.get_text("dict")["blocks"]
                                extracted = next(
                                    (
                                        block
                                        for block in inline_blocks
                                        if block.get("type") == 1 and block.get("number") == item["number"]
                                    ),
                                    {},
                                )
                            data = extracted.get("image", b"")
                            extension = extracted.get("ext", "png").lower()
                            mime_type = "image/jpeg" if extension in ("jpg", "jpeg") else f"image/{extension}"
                            if not data:
                                raise ValueError("Image payload unavailable")
                        except Exception as exc:
                            if warnings is not None:
                                warnings.append(f"Page {page_number} image payload unavailable: {exc}")
                    sha256 = hashlib.sha256(data).hexdigest() if data else ""
                    payload_key = sha256 or identity
                    asset_id = identity_to_asset.get(payload_key)
                    if asset_id is None:
                        total_bytes += len(data)
                        if total_bytes > max_asset_bytes:
                            raise PDFEngineError(
                                ErrorCode.PDF_RESOURCE_LIMIT, "Visual assets exceed max_asset_bytes."
                            )
                        asset_id = f"asset_img_{len(assets) + 1:04d}"
                        path = f"{relative_asset_dir}/{asset_id}.{extension}" if data else None
                        source = SourceProvenance(
                            method=SourceMethod.EMBEDDED_PDF_IMAGE, engine="pymupdf", version=fitz.__version__
                        )
                        raw = ExtractedImageAsset(
                            asset_id, sha256, data, extension, mime_type, item["width"], item["height"], []
                        )
                        asset = Asset(
                            id=asset_id,
                            type=AssetType.IMAGE,
                            path=path,
                            mime_type=mime_type,
                            sha256=sha256,
                            width=item["width"],
                            height=item["height"],
                            source=source,
                        )
                        assets[asset_id] = (raw, asset)
                        occurrences[asset_id] = set()
                        identity_to_asset[payload_key] = asset_id
                    identity_to_asset[identity] = asset_id
                rect = fitz.Rect(item["bbox"])
                bounds = (rect.x0, rect.y0, rect.x1, rect.y1)
                position = (page_number, *bounds)
                if position not in occurrences[asset_id]:
                    bbox = (
                        BoundingBox.from_rect(bounds) if not rect.is_empty and not rect.is_infinite else None
                    )
                    occurrence = AssetOccurrence(page=page_number, bbox=bbox)
                    raw, asset = assets[asset_id]
                    raw.occurrences.append(occurrence)
                    asset.occurrences.append(occurrence)
                    occurrences[asset_id].add(position)
        return assets

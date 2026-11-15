from __future__ import annotations

import hashlib
import logging
from io import BytesIO
from typing import TYPE_CHECKING

import pymupdf as fitz  # PyMuPDF
from PIL import Image

from trueparse.exceptions import PDFEngineError
from trueparse.extraction.pdf.drawings import DrawingInspector
from trueparse.extraction.pdf.images import ExtractedImageAsset
from trueparse.extraction.pdf.renderer import PDFRenderer
from trueparse.models.assets import Asset, AssetOccurrence
from trueparse.models.elements import FigureElement, GenericElement, SourceProvenance
from trueparse.models.enums import (
    AssetType,
    ElementType,
    ErrorCode,
    ImageInclusionMode,
    SourceMethod,
)
from trueparse.processing.spatial import inside_any

if TYPE_CHECKING:
    from trueparse.pipeline.runner import PDFParser

logger = logging.getLogger("trueparse")


def extract_vector_figures(
    parser: PDFParser,
    page: fitz.Page,
    page_num: int,
    doc_id: str,
    table_elements: list,
    public_assets: dict[str, Asset],
    page_asset_ids: list[str],
    page_elements: list[GenericElement],
    fig_counter: int,
    vector_hash_to_asset: dict[str, str],
    raw_asset_bytes: dict[str, bytes],
    save_to_disk: bool,
) -> int:
    drawings_info = DrawingInspector.inspect_page(page, page_num)
    img_mode = parser.options.output_config.json_options.images
    if not parser._deliver_visual_bytes:
        img_mode = ImageInclusionMode.NONE

    for cluster_bbox in drawings_info.clusters:
        if inside_any(cluster_bbox, table_elements, ratio=0.8):
            continue

        if img_mode == ImageInclusionMode.NONE:
            page_elements.append(
                FigureElement(
                    id=f"fig_p{page_num:04d}_{fig_counter:04d}",
                    page=page_num,
                    bbox=cluster_bbox,
                    reading_order=1,
                    content="",
                    provenance=SourceProvenance(method=SourceMethod.VECTOR_CROP, engine="pymupdf_drawings"),
                )
            )
            fig_counter += 1
            continue

        try:
            parser.budget.reserve_render(page, parser.profile.render_dpi, cluster_bbox)
            crop_bytes = PDFRenderer.render_region_to_bytes(
                page=page, bbox=cluster_bbox, dpi=parser.profile.render_dpi, format="PNG"
            )
        except Exception as exc:
            if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                raise
            logger.warning(f"[Page {page_num:02d}] Vector crop render failed: {exc}")
            parser._asset_warnings.append(f"Page {page_num}: vector crop failed: {exc}")
            page_elements.append(
                FigureElement(
                    id=f"fig_p{page_num:04d}_{fig_counter:04d}",
                    page=page_num,
                    bbox=cluster_bbox,
                    reading_order=1,
                    content="",
                    provenance=SourceProvenance(method=SourceMethod.VECTOR_CROP, engine="pymupdf_drawings"),
                )
            )
            fig_counter += 1
            continue

        crop_sha = hashlib.sha256(crop_bytes).hexdigest()
        with Image.open(BytesIO(crop_bytes)) as crop_image:
            pixel_width, pixel_height = crop_image.size
        existing_id = vector_hash_to_asset.get(crop_sha)

        if existing_id is not None:
            crop_asset = public_assets[existing_id]
            crop_asset.occurrences.append(AssetOccurrence(page=page_num, bbox=cluster_bbox))
            crop_asset_id = existing_id
            crop_rel_path = crop_asset.path
        else:
            vec_fig_idx = len(vector_hash_to_asset) + 1
            crop_asset_id = f"asset_fig_{vec_fig_idx:04d}"
            parser.budget.reserve_asset(len(crop_bytes))
            crop_rel_path = f"assets/figures/{crop_asset_id}.png"
            raw_asset_bytes[crop_asset_id] = crop_bytes

            if save_to_disk and img_mode == ImageInclusionMode.PATH:
                parser.storage.save_image_asset(
                    ExtractedImageAsset(
                        asset_id=crop_asset_id,
                        sha256=crop_sha,
                        image_bytes=crop_bytes,
                        ext="png",
                        mime_type="image/png",
                        width=pixel_width,
                        height=pixel_height,
                        occurrences=[],
                    ),
                    doc_id,
                    subfolder="figures",
                )

            public_assets[crop_asset_id] = Asset(
                id=crop_asset_id,
                type=AssetType.FIGURE,
                path=crop_rel_path if img_mode != ImageInclusionMode.BASE64 else None,
                mime_type="image/png",
                sha256=crop_sha,
                width=pixel_width,
                height=pixel_height,
                occurrences=[AssetOccurrence(page=page_num, bbox=cluster_bbox)],
                source=SourceProvenance(
                    method=SourceMethod.VECTOR_CROP,
                    engine="pymupdf_render",
                    confidence=0.90,
                ),
            )
            vector_hash_to_asset[crop_sha] = crop_asset_id

        page_asset_ids.append(crop_asset_id)
        page_elements.append(
            FigureElement(
                id=f"fig_p{page_num:04d}_{fig_counter:04d}",
                type=ElementType.FIGURE,
                page=page_num,
                bbox=cluster_bbox,
                reading_order=1,
                content="",
                confidence=0.90,
                provenance=SourceProvenance(
                    method=SourceMethod.VECTOR_CROP,
                    engine="pymupdf_drawings",
                    confidence=0.90,
                ),
                asset_id=crop_asset_id,
                asset_path=crop_rel_path,
            )
        )
        fig_counter += 1

    return fig_counter

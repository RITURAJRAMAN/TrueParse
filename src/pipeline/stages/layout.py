from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pymupdf as fitz  # PyMuPDF

from trueparse.contracts.document import Issue
from trueparse.exceptions import PDFEngineError
from trueparse.extraction.pdf.renderer import PDFRenderer
from trueparse.models.document import Page
from trueparse.models.elements import DocumentElement, FigureElement, GenericElement, SourceProvenance
from trueparse.models.enums import (
    AssetType,
    ElementType,
    ErrorCode,
    ImageInclusionMode,
    SourceMethod,
)
from trueparse.models.text import RawTextBlock
from trueparse.pipeline.stages.visuals import extract_vector_figures
from trueparse.processing.classification import TextClassifier
from trueparse.processing.reading_order import ReadingOrderEngine
from trueparse.processing.spatial import inside_any

if TYPE_CHECKING:
    from trueparse.pipeline.runner import PDFParser

logger = logging.getLogger("trueparse")


def build_pages(
    parser: PDFParser,
    doc,
    doc_id,
    inspection,
    scans,
    font_profile,
    public_assets,
    raw_asset_bytes,
    save_to_disk,
    progress_callback,
    start_time,
):
    pages_models: list[Page] = []
    pages_raw_elements: list[list[GenericElement]] = []
    vector_hash_to_asset: dict[str, str] = {}
    img_mode = parser.options.output_config.json_options.images
    if not getattr(parser, "_deliver_visual_bytes", True):
        img_mode = ImageInclusionMode.NONE
    image_placements: dict[int, list[tuple]] = {}
    for asset_id, asset in public_assets.items():
        if asset.type == AssetType.IMAGE:
            for occurrence in asset.occurrences:
                image_placements.setdefault(occurrence.page, []).append((asset_id, asset, occurrence))
    for scan in scans:
        parser._check_timeout(start_time, "building_page")
        page_num = scan.page_number
        forensics = inspection.pages[page_num - 1]
        page = None
        try:
            page = doc[page_num - 1]
        except Exception as exc:
            if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                raise
            scan.scan_failed = True
            scan.scan_error = scan.scan_error or f"Page {page_num} loading failed during layout: {exc}"
            parser._issues.append(
                Issue(code="PAGE_LAYOUT_FAILED", stage="layout", page=page_num, message=str(exc))
            )
        page_elements: list[GenericElement] = []
        page_asset_ids: list[str] = []
        for index, block in enumerate(scan.raw_blocks, 1):
            if inside_any(block.bbox, scan.tables, ratio=0.5):
                continue
            try:
                elements = parser._timed(
                    "classification",
                    TextClassifier.classify_and_build_elements,
                    raw_blocks=[block],
                    body_font_size=scan.page_body_font,
                    page_height=forensics.height,
                    font_profile=font_profile,
                    detect_formulas=parser.options.extract_formulas,
                    start_index=index,
                )
                if block.ocr_confidence is not None:
                    tag_ocr_provenance(elements, [block])
                page_elements.extend(elements)
            except Exception as exc:
                if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                    raise
                scan.scan_failed = True
                message = f"Page {page_num} block {index} classification failed: {exc}"
                scan.scan_error = scan.scan_error or message
                scan.warnings.append(message)
                fallback = DocumentElement(
                    id=f"unknown_p{page_num:04d}_{index:04d}",
                    type=ElementType.UNKNOWN,
                    page=page_num,
                    bbox=block.bbox,
                    reading_order=index,
                    content=block.text,
                    confidence=0.0,
                    metadata={"recovery_reason": "classification_failed"},
                )
                if block.ocr_confidence is not None:
                    tag_ocr_provenance([fallback], [block])
                page_elements.append(fallback)
                parser._issues.append(
                    Issue(
                        code="BLOCK_CLASSIFICATION_FAILED",
                        stage="classification",
                        page=page_num,
                        message=message,
                        node_ids=[fallback.id],
                    )
                )
        page_elements.extend(scan.tables)
        for table in scan.tables:
            table.metadata.setdefault("page_height", forensics.height)
            table.metadata.setdefault("page_width", forensics.width)
        fig_counter = 1
        for asset_id, asset, occurrence in image_placements.get(page_num, ()):
            try:
                parent = next(
                    (table.id for table in scan.tables if inside_any(occurrence.bbox, [table], ratio=0.5)),
                    None,
                )
                page_elements.append(
                    FigureElement(
                        id=f"fig_p{page_num:04d}_{fig_counter:04d}",
                        page=page_num,
                        bbox=occurrence.bbox,
                        reading_order=1,
                        content="",
                        asset_id=asset_id,
                        asset_path=asset.path if img_mode != ImageInclusionMode.NONE else None,
                        metadata={"parent_table_id": parent} if parent else {},
                        provenance=SourceProvenance(
                            method=SourceMethod.EMBEDDED_PDF_IMAGE,
                            engine="pymupdf",
                            version=fitz.__version__,
                            confidence=1.0,
                        ),
                    )
                )
                page_asset_ids.append(asset_id)
                fig_counter += 1
            except Exception as exc:
                if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                    raise
                scan.warnings.append(f"Page {page_num} image {asset_id} placement failed: {exc}")
                parser._issues.append(
                    Issue(
                        code="IMAGE_PLACEMENT_FAILED",
                        stage="assets",
                        page=page_num,
                        message=str(exc),
                    )
                )
        if page is not None and parser.options.extract_charts:
            try:
                parser._timed(
                    "vector_extraction",
                    extract_vector_figures,
                    parser,
                    page=page,
                    page_num=page_num,
                    doc_id=doc_id,
                    table_elements=scan.tables,
                    public_assets=public_assets,
                    page_asset_ids=page_asset_ids,
                    page_elements=page_elements,
                    fig_counter=fig_counter,
                    vector_hash_to_asset=vector_hash_to_asset,
                    raw_asset_bytes=raw_asset_bytes,
                    save_to_disk=save_to_disk,
                )
            except Exception as exc:
                if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                    raise
                scan.warnings.append(f"Page {page_num} vector extraction failed: {exc}")
                parser._issues.append(
                    Issue(code="VECTOR_EXTRACTION_FAILED", stage="assets", page=page_num, message=str(exc))
                )
        if page is not None and parser.options.debug and save_to_disk:
            try:
                parser.budget.reserve_render(page, parser.profile.render_dpi)
                target = (
                    parser.storage.get_document_dir(doc_id) / "debug" / "pages" / f"page_{page_num:04d}.png"
                )
                parser._timed(
                    "debug_render",
                    PDFRenderer.render_page_to_file,
                    page,
                    target,
                    dpi=parser.profile.render_dpi,
                )
            except Exception as exc:
                if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                    raise
                scan.warnings.append(f"Page {page_num} debug rendering failed: {exc}")
        try:
            ordered = parser._timed(
                "reading_order",
                ReadingOrderEngine.order_page_elements,
                elements=page_elements,
                page_width=forensics.width,
            )
        except Exception as exc:
            if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                raise
            scan.warnings.append(f"Page {page_num} reading order failed: {exc}; recovered extraction order.")
            parser._issues.append(
                Issue(code="READING_ORDER_FALLBACK", stage="reading_order", page=page_num, message=str(exc))
            )
            ordered = page_elements
            for index, element in enumerate(ordered, 1):
                element.reading_order = index
        pages_raw_elements.append(ordered)
        pages_models.append(
            Page(
                page_number=page_num,
                width=forensics.width,
                height=forensics.height,
                rotation=forensics.rotation,
                elements=ordered,
                asset_ids=sorted(set(page_asset_ids)),
            )
        )
        if progress_callback:
            progress_callback(page_num, len(scans), f"building_page_{page_num}")
    return pages_models, pages_raw_elements


def tag_ocr_provenance(
    elements: list[DocumentElement],
    blocks: list[RawTextBlock],
) -> None:
    for element, block in zip(elements, blocks, strict=True):
        confidence = block.ocr_confidence
        if confidence is None:
            continue
        element.provenance = SourceProvenance(
            method=SourceMethod.OCR_MODEL,
            engine="rapidocr",
            confidence=confidence,
        )
        element.confidence = confidence
        element.metadata["ocr"] = True

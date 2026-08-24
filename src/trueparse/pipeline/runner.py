from __future__ import annotations
from pathlib import Path
from typing import Optional, Callable
import pymupdf as fitz  # PyMuPDF
import logging
import time

from trueparse.core.logging import setup_logging
from trueparse.core.config import ParseOptions, EngineConfig
from trueparse.core.enums import ElementType, AssetType, SourceMethod
from trueparse.core.errors import PDFEngineError
from trueparse.core.models import (
    Asset,
    AssetOccurrence,
    Document,
    DocumentElement,
    FigureElement,
    GenericElement,
    Page,
    SourceProvenance,
    BoundingBox,
)
from trueparse.pdf.inspector import PDFInspector, DocumentInspection
from trueparse.pdf.images import ImageExtractor, ExtractedImageAsset
from trueparse.pdf.native import NativeExtractor
from trueparse.pdf.drawings import DrawingInspector
from trueparse.pdf.renderer import PDFRenderer
from trueparse.tables.native import NativeTableExtractor
from trueparse.document.reading_order import ReadingOrderEngine
from trueparse.document.hierarchy import HierarchyEngine
from trueparse.document.graph import DocumentGraphBuilder
from trueparse.quality.confidence import QualityEngine
from trueparse.storage.filesystem import FileSystemStorage
from trueparse.serializer.json import JSONSerializer
from trueparse.serializer.markdown import MarkdownExporter

logger = logging.getLogger("ParsingEngine")


class PDFParser:
    """Main TrueParse orchestrator."""

    def __init__(
        self,
        options: Optional[ParseOptions] = None,
        config: Optional[EngineConfig] = None,
    ):
        self.options = options or ParseOptions()
        self.config = config or EngineConfig()
        output_root = self.options.output_path or self.config.default_output_root
        self.storage = FileSystemStorage(root_path=output_root)

    def parse(
        self,
        file_path: str | Path,
        original_filename: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Document:
        start_time = time.time()
        path = Path(file_path).resolve()
        source_name = original_filename or path.name
        logger.info(f"=== Starting PDF parsing for: {source_name} ===")

        if progress_callback:
            progress_callback(0, 1, "forensics")

        # 1. Intake & Forensics
        inspection = PDFInspector.inspect(
            file_path=path,
            max_file_size_mb=self.options.max_file_size_mb,
        )
        doc_id = inspection.document_id
        logger.info(
            f"Forensics: Document ID={doc_id} | Pages={inspection.page_count} | "
            f"Size={inspection.file_size_bytes / (1024*1024):.2f}MB | "
            f"NativeText={inspection.overall_native_text} | LikelyScan={inspection.overall_likely_scan}"
        )

        # Prepare storage directory structure
        dirs = self.storage.prepare_directory_structure(doc_id)
        saved_source = self.storage.save_source_pdf(
            source_path=path,
            document_id=doc_id,
            original_filename=source_name,
        )
        logger.info(f"Source PDF saved to: {saved_source}")

        # Open doc for processing
        doc = fitz.open(path)
        total_pages = len(doc)
        if self.options.max_pages and self.options.max_pages > 0:
            total_pages = min(total_pages, self.options.max_pages)
            logger.info(f"Page limit applied: processing first {total_pages} of {len(doc)} pages")

        if progress_callback:
            progress_callback(0, total_pages, "extracting_assets")

        # 2. Extract and deduplicate all embedded raster images
        extracted_assets_map: dict[str, tuple[ExtractedImageAsset, Asset]] = {}
        if self.options.extract_images:
            extracted_assets_map = ImageExtractor.extract_embedded_images(
                doc, relative_asset_dir="assets/images"
            )
            # Save extracted raster assets to disk
            for asset_id, (extracted_img, _) in extracted_assets_map.items():
                self.storage.save_image_asset(
                    extracted=extracted_img,
                    document_id=doc_id,
                    subfolder="images"
                )
            logger.info(f"Asset Extraction: Found {len(extracted_assets_map)} unique embedded raster images (deduplicated by SHA-256)")

        public_assets: dict[str, Asset] = {
            k: v[1] for k, v in extracted_assets_map.items()
        }

        # 3. Process pages
        pages_raw_elements: list[list[GenericElement]] = []
        pages_models: list[Page] = []

        for page_idx in range(total_pages):
            page_num = page_idx + 1
            page = doc[page_idx]
            forensics = inspection.pages[page_idx]

            page_elements: list[GenericElement] = []
            page_asset_ids: list[str] = []

            # (A) Native Table Extraction
            table_elements: list[GenericElement] = []
            if self.options.extract_tables:
                table_elements = NativeTableExtractor.extract_page_tables(
                    page=page, page_number=page_num, start_index=1
                )

            # (B) Native Text Extraction & Classification (excluding text inside tables)
            raw_blocks, body_font_size = NativeExtractor.extract_page_text_blocks(
                page=page, page_number=page_num
            )
            # Filter out raw blocks that fall within detected table boundaries
            non_table_blocks = []
            for b in raw_blocks:
                b_rect = fitz.Rect(b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1)
                is_inside_table = any(
                    (b_rect & fitz.Rect(t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1)).get_area() > 0.5 * b_rect.get_area()
                    for t in table_elements
                )
                if not is_inside_table:
                    non_table_blocks.append(b)

            text_elements = NativeExtractor.classify_and_build_elements(
                raw_blocks=non_table_blocks,
                body_font_size=body_font_size,
                page_height=forensics.height,
                page_width=forensics.width,
            )
            page_elements.extend(text_elements)
            page_elements.extend(table_elements)

            # (C) Map embedded images on this page to FigureElements (excluding images inside tables)
            fig_counter = 1
            for asset_id, asset_model in public_assets.items():
                if asset_model.type == AssetType.IMAGE:
                    for occ in asset_model.occurrences:
                        if occ.page == page_num:
                            # Check if image is inside any detected table
                            occ_rect = fitz.Rect(occ.bbox.x0, occ.bbox.y0, occ.bbox.x1, occ.bbox.y1)
                            is_inside_table = any(
                                (occ_rect & fitz.Rect(t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1)).get_area() > 0.5 * occ_rect.get_area()
                                for t in table_elements
                            )
                            if is_inside_table:
                                continue

                            page_asset_ids.append(asset_id)
                            fig_elem = FigureElement(
                                id=f"fig_p{page_num:04d}_{fig_counter:04d}",
                                type=ElementType.FIGURE,
                                page=page_num,
                                bbox=occ.bbox,
                                reading_order=1,
                                content=f"[Embedded Image: {asset_model.path}]",
                                confidence=1.0,
                                provenance=SourceProvenance(
                                    method=SourceMethod.EMBEDDED_PDF_IMAGE,
                                    engine="pymupdf",
                                    version=fitz.__version__,
                                    confidence=1.0,
                                ),
                                asset_id=asset_id,
                                asset_path=asset_model.path,
                                title=None,
                            )
                            page_elements.append(fig_elem)
                            fig_counter += 1

            # (D) Vector Drawing Visual Candidates (render region crop only)
            if self.options.extract_charts:
                drawings_info = DrawingInspector.inspect_page(page, page_num)
                for cluster_bbox in drawings_info.clusters:
                    # Check if cluster is simply part of a table border/grid
                    c_rect = fitz.Rect(cluster_bbox.x0, cluster_bbox.y0, cluster_bbox.x1, cluster_bbox.y1)
                    is_inside_table = False
                    for t in table_elements:
                        t_rect = fitz.Rect(t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1)
                        if (c_rect & t_rect).get_area() > 0.8 * c_rect.get_area():
                            is_inside_table = True
                            break

                    if is_inside_table:
                        continue

                    # Count vector figures for standardized asset naming
                    vec_fig_idx = len([a for a in public_assets.values() if a.type == AssetType.FIGURE]) + 1
                    crop_asset_id = f"asset_fig_{vec_fig_idx:04d}"

                    # Render only the detected vector bounding region
                    crop_bytes = PDFRenderer.render_region_to_bytes(
                        page=page,
                        bbox=cluster_bbox,
                        dpi=self.options.render_dpi,
                        format="PNG",
                    )
                    extracted_crop = ExtractedImageAsset(
                        asset_id=crop_asset_id,
                        sha256="",
                        image_bytes=crop_bytes,
                        ext="png",
                        mime_type="image/png",
                        width=int(cluster_bbox.width),
                        height=int(cluster_bbox.height),
                        occurrences=[],
                    )
                    self.storage.save_image_asset(extracted_crop, doc_id, subfolder="figures")

                    crop_rel_path = f"assets/figures/{crop_asset_id}.png"
                    crop_asset = Asset(
                        id=crop_asset_id,
                        type=AssetType.FIGURE,
                        path=crop_rel_path,
                        mime_type="image/png",
                        sha256="",
                        width=int(cluster_bbox.width),
                        height=int(cluster_bbox.height),
                        occurrences=[AssetOccurrence(page=page_num, bbox=cluster_bbox)],
                        source=SourceProvenance(
                            method=SourceMethod.VECTOR_CROP,
                            engine="pymupdf_render",
                            confidence=0.90,
                        ),
                    )
                    public_assets[crop_asset_id] = crop_asset
                    page_asset_ids.append(crop_asset_id)

                    fig_elem = FigureElement(
                        id=f"fig_p{page_num:04d}_{fig_counter:04d}",
                        type=ElementType.FIGURE,
                        page=page_num,
                        bbox=cluster_bbox,
                        reading_order=1,
                        content=f"[Vector Graphic: {crop_rel_path}]",
                        confidence=0.90,
                        provenance=SourceProvenance(
                            method=SourceMethod.VECTOR_CROP,
                            engine="pymupdf_drawings",
                            confidence=0.90,
                        ),
                        asset_id=crop_asset_id,
                        asset_path=crop_rel_path,
                    )
                    page_elements.append(fig_elem)
                    fig_counter += 1

            # (E) Debug Page Rendering (only if debug mode is explicitly on)
            if self.options.debug:
                debug_page_file = dirs["debug_pages"] / f"page_{page_num:04d}.png"
                PDFRenderer.render_page_to_file(page, debug_page_file, dpi=self.options.render_dpi)

            # (F) Reading Order Sorting
            ordered_elements = ReadingOrderEngine.order_page_elements(
                elements=page_elements,
                page_width=forensics.width,
                page_height=forensics.height,
            )
            pages_raw_elements.append(ordered_elements)

            # (G) Page Quality Evaluation
            p_quality = QualityEngine.evaluate_page(
                page_num=page_num,
                text_elements_count=len(text_elements),
                tables_count=len(table_elements) if self.options.extract_tables else 0,
                has_native_text=forensics.has_native_text,
                likely_scan=forensics.likely_scan,
            )

            pages_models.append(
                Page(
                    page_number=page_num,
                    width=forensics.width,
                    height=forensics.height,
                    rotation=forensics.rotation,
                    elements=ordered_elements,
                    asset_ids=list(set(page_asset_ids)),
                    quality=p_quality,
                )
            )

            logger.info(
                f"[Page {page_num:02d}/{total_pages:02d}] Extracted {len(text_elements)} text blocks, "
                f"{len(table_elements)} tables, {len(page_asset_ids)} assets (Words: {forensics.word_count})"
            )
            if progress_callback:
                progress_callback(page_num, total_pages, f"page_{page_num}")

        doc.close()

        if progress_callback:
            progress_callback(total_pages, total_pages, "building_hierarchy")

        # 4. Build Sections, Headings Hierarchy & Caption Associations
        sections, updated_pages_elements = HierarchyEngine.build_sections_and_captions(
            pages_raw_elements
        )

        for p_idx, page_obj in enumerate(pages_models):
            page_obj.elements = updated_pages_elements[p_idx]

        # 5. Build Relational Document Graph
        relationships = DocumentGraphBuilder.build_relationships(
            sections=sections,
            pages_elements=updated_pages_elements,
        )

        # 6. Overall Document Quality
        doc_quality = QualityEngine.evaluate_document(pages_models)

        # 7. Construct Document
        final_doc = Document(
            id=doc_id,
            schema_version=self.config.schema_version,
            engine_version=self.config.engine_version,
            source_file=source_name,
            metadata=inspection.metadata,
            pages=pages_models,
            sections=sections,
            assets=public_assets,
            relationships=relationships,
            quality=doc_quality,
            warnings=doc_quality.warnings,
        )

        # 8. Save canonical document.json & optional markdown
        doc_json_str = JSONSerializer.serialize(final_doc)
        json_file = self.storage.save_document_json(doc_json_str, doc_id)

        md_content = MarkdownExporter.export(final_doc)
        md_file = dirs["output"] / "document.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_content)

        elapsed = time.time() - start_time
        logger.info(f"Serialization: document.json saved to {json_file}")
        logger.info(f"Serialization: document.md saved to {md_file}")
        logger.info(
            f"=== Parse Completed: Document ID={doc_id} | Pages={len(pages_models)} | "
            f"Assets={len(public_assets)} | Sections={len(sections)} | Quality={doc_quality.overall_score:.2f} | Time={elapsed:.2f}s ==="
        )
        return final_doc

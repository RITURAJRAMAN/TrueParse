from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from trueparse.chunking.chunker import DocumentChunker
from trueparse.core.config import EngineConfig, ParseOptions
from trueparse.core.enums import (
    AssetType,
    ElementType,
    ErrorCode,
    OCRMode,
    SourceMethod,
)
from trueparse.core.errors import PDFEngineError
from trueparse.core.models import (
    Asset,
    AssetOccurrence,
    BoundingBox,
    Document,
    DocumentElement,
    FigureElement,
    GenericElement,
    Page,
    SourceProvenance,
)
from trueparse.core.profiles import ProfileSettings
from trueparse.core.profiles import resolve as resolve_profile
from trueparse.document.graph import DocumentGraphBuilder
from trueparse.document.hierarchy import HierarchyEngine
from trueparse.document.reading_order import ReadingOrderEngine
from trueparse.document.text_merge import merge_paragraphs
from trueparse.ocr.engine import OCREngine, OCRPageResult
from trueparse.pdf.drawings import DrawingInspector
from trueparse.pdf.images import ExtractedImageAsset, ImageExtractor
from trueparse.pdf.inspector import PDFInspector
from trueparse.pdf.native import NativeExtractor, RawTextBlock
from trueparse.pdf.renderer import PDFRenderer
from trueparse.quality.confidence import QualityEngine
from trueparse.serializer.html import HTMLExporter, TextExporter
from trueparse.serializer.json import JSONSerializer
from trueparse.serializer.markdown import MarkdownExporter
from trueparse.storage.filesystem import FileSystemStorage
from trueparse.tables.native import NativeTableExtractor

logger = logging.getLogger("trueparse")


@dataclass
class _PageScan:
    """What the first pass recovered from one page.

    The pipeline reads every page before classifying any of them, because
    heading detection needs typography statistics for the whole document.
    """
    page_number: int
    raw_blocks: list[RawTextBlock] = field(default_factory=list)
    tables: list = field(default_factory=list)
    page_body_font: float = 10.0
    ocr_applied: bool = False
    ocr_confidence: float | None = None


class PDFParser:
    """Main TrueParse orchestrator."""

    def __init__(
        self,
        options: ParseOptions | None = None,
        config: EngineConfig | None = None,
    ):
        self.options = options or ParseOptions()
        self.config = config or EngineConfig()
        self.profile: ProfileSettings = resolve_profile(self.options.profile)
        # An explicit render_dpi overrides the profile's choice.
        if self.options.render_dpi:
            self.profile.render_dpi = self.options.render_dpi
        output_root = self.options.output_path or self.config.default_output_root
        self.storage = FileSystemStorage(root_path=output_root)
        self._ocr_warning_emitted = False

    def parse(
        self,
        file_path: str | Path,
        original_filename: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> Document:
        start_time = time.time()
        path = Path(file_path).resolve()
        source_name = original_filename or path.name
        logger.info(f"=== Starting PDF parsing for: {source_name} ===")
        logger.info(
            f"Profile '{self.options.profile.value}': dpi={self.profile.render_dpi} "
            f"tables={'+'.join(self.profile.table_strategies)} "
            f"spans={self.profile.detect_table_spans} ocr={self.options.ocr.value}"
        )

        if progress_callback:
            progress_callback(0, 1, "forensics")

        # 1. Intake & Forensics
        inspection = PDFInspector.inspect(
            file_path=path,
            max_file_size_mb=self.options.max_file_size_mb,
            password=self.options.password,
        )
        doc_id = inspection.document_id
        logger.info(
            f"Forensics: Document ID={doc_id} | Pages={inspection.page_count} | "
            f"Size={inspection.file_size_bytes / (1024*1024):.2f}MB | "
            f"NativeText={inspection.overall_native_text} | LikelyScan={inspection.overall_likely_scan}"
        )

        dirs = self.storage.prepare_directory_structure(doc_id)
        saved_source = self.storage.save_source_pdf(
            source_path=path,
            document_id=doc_id,
            original_filename=source_name,
        )
        logger.info(f"Source PDF saved to: {saved_source}")

        doc = fitz.open(path)
        if doc.is_encrypted and not doc.authenticate(self.options.password or ""):
            doc.close()
            raise PDFEngineError(
                code=ErrorCode.PDF_PASSWORD_REQUIRED,
                message="PDF is encrypted and the supplied password did not unlock it.",
                document_id=doc_id,
            )

        try:
            return self._parse_open_document(
                doc=doc,
                doc_id=doc_id,
                inspection=inspection,
                dirs=dirs,
                source_name=source_name,
                start_time=start_time,
                progress_callback=progress_callback,
            )
        finally:
            doc.close()

    def _parse_open_document(
        self,
        doc: fitz.Document,
        doc_id: str,
        inspection,
        dirs: dict[str, Path],
        source_name: str,
        start_time: float,
        progress_callback: Callable[[int, int, str], None] | None,
    ) -> Document:
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
            for extracted_img, _ in extracted_assets_map.values():
                self.storage.save_image_asset(
                    extracted=extracted_img, document_id=doc_id, subfolder="images"
                )
            logger.info(
                f"Asset Extraction: Found {len(extracted_assets_map)} unique embedded "
                "raster images (deduplicated by SHA-256)"
            )

        public_assets: dict[str, Asset] = {k: v[1] for k, v in extracted_assets_map.items()}

        # 3. First pass: read text and tables from every page.
        scans = self._scan_pages(doc, inspection, total_pages, progress_callback)

        # 4. Document-wide typography, computed once from every block.
        all_blocks = [b for scan in scans for b in scan.raw_blocks]
        font_profile = NativeExtractor.build_font_profile(all_blocks)
        logger.info(
            f"Typography: body={font_profile.body_size}pt "
            f"heading_ladder={font_profile.ladder or 'none detected'}"
        )

        # 5. Second pass: classify, place assets, order, and score.
        pages_models, pages_raw_elements = self._build_pages(
            doc=doc,
            doc_id=doc_id,
            inspection=inspection,
            scans=scans,
            font_profile=font_profile,
            public_assets=public_assets,
            dirs=dirs,
            progress_callback=progress_callback,
        )

        if progress_callback:
            progress_callback(total_pages, total_pages, "building_hierarchy")

        # 6. Rejoin prose split across columns and page breaks.
        if self.profile.merge_paragraphs:
            before = sum(len(p) for p in pages_raw_elements)
            pages_raw_elements = merge_paragraphs(pages_raw_elements)
            after = sum(len(p) for p in pages_raw_elements)
            if before != after:
                logger.info(f"Layout: merged {before - after} split paragraph fragments")

        # 7. Join tables that continue onto the following page.
        if self.options.extract_tables and self.profile.merge_cross_page_tables:
            before = sum(
                1 for page in pages_raw_elements for e in page if e.type == ElementType.TABLE
            )
            pages_raw_elements = NativeTableExtractor.merge_cross_page_tables(pages_raw_elements)
            after = sum(
                1 for page in pages_raw_elements for e in page if e.type == ElementType.TABLE
            )
            if before != after:
                logger.info(f"Tables: merged {before - after} cross-page table continuations")

        # 8. Sections, heading hierarchy & caption associations
        sections, updated_pages_elements = HierarchyEngine.build_sections_and_captions(
            pages_raw_elements
        )
        for p_idx, page_obj in enumerate(pages_models):
            page_obj.elements = updated_pages_elements[p_idx]

        # 9. Relational document graph
        relationships = DocumentGraphBuilder.build_relationships(
            sections=sections, pages_elements=updated_pages_elements
        )

        # 10. Re-score pages now that elements are final, then score the document.
        for page_obj, scan in zip(pages_models, scans, strict=True):
            forensics = inspection.pages[page_obj.page_number - 1]
            page_obj.quality = QualityEngine.evaluate_page(
                page_num=page_obj.page_number,
                text_elements_count=sum(
                    1 for e in page_obj.elements
                    if e.type not in (ElementType.TABLE, ElementType.FIGURE)
                ),
                tables_count=sum(
                    1 for e in page_obj.elements if e.type == ElementType.TABLE
                ),
                has_native_text=forensics.has_native_text,
                likely_scan=forensics.likely_scan,
                elements=page_obj.elements,
                page_width=page_obj.width,
                page_height=page_obj.height,
                ocr_applied=scan.ocr_applied,
                ocr_confidence=scan.ocr_confidence,
            )

        doc_quality = QualityEngine.evaluate_document(pages_models)

        # 11. Construct Document
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

        # 12. Serialise every requested output format
        self._write_outputs(final_doc, doc_id, dirs)

        elapsed = time.time() - start_time
        logger.info(
            f"=== Parse Completed: Document ID={doc_id} | Pages={len(pages_models)} | "
            f"Assets={len(public_assets)} | Sections={len(sections)} | "
            f"Quality={doc_quality.overall_score:.2f} | Time={elapsed:.2f}s ==="
        )
        return final_doc

    def _scan_pages(
        self,
        doc: fitz.Document,
        inspection,
        total_pages: int,
        progress_callback: Callable[[int, int, str], None] | None,
    ) -> list[_PageScan]:
        scans: list[_PageScan] = []

        for page_idx in range(total_pages):
            page_num = page_idx + 1
            page = doc[page_idx]
            forensics = inspection.pages[page_idx]
            scan = _PageScan(page_number=page_num)

            if self.options.extract_tables:
                scan.tables = NativeTableExtractor.extract_page_tables(
                    page=page,
                    page_number=page_num,
                    start_index=1,
                    strategies=self.profile.table_strategies,
                    detect_spans=self.profile.detect_table_spans,
                )

            scan.raw_blocks, scan.page_body_font = NativeExtractor.extract_page_text_blocks(
                page=page, page_number=page_num
            )

            if self._should_ocr(forensics, scan.raw_blocks):
                ocr_blocks, ocr_result = self._ocr_page(page, page_num)
                if ocr_blocks:
                    scan.raw_blocks.extend(ocr_blocks)
                    scan.ocr_applied = True
                    scan.ocr_confidence = ocr_result.mean_confidence
                    logger.info(
                        f"[Page {page_num:02d}] OCR recovered {len(ocr_blocks)} text blocks "
                        f"(mean confidence {ocr_result.mean_confidence:.2f})"
                    )

            scans.append(scan)
            if progress_callback:
                progress_callback(page_num, total_pages, f"scanning_page_{page_num}")

        return scans

    def _should_ocr(self, forensics, raw_blocks: list[RawTextBlock]) -> bool:
        """Decides whether this page needs OCR under the active mode and profile."""
        if self.options.ocr == OCRMode.NEVER or self.profile.ocr_floor == OCRMode.NEVER:
            return False
        if not OCREngine.is_available():
            self._warn_ocr_unavailable_once()
            return False
        if self.options.ocr == OCRMode.ALWAYS:
            return True

        # AUTO: only pages native extraction clearly failed on. A page that
        # yielded real text is never re-OCR'd just for being short.
        if forensics.likely_scan:
            return True
        if forensics.has_native_text:
            return False
        native_chars = sum(b.char_count for b in raw_blocks)
        return native_chars < self.profile.ocr_min_chars

    def _warn_ocr_unavailable_once(self) -> None:
        """Notes the missing OCR extra once per parse, not once per page.

        Logged at WARNING only when OCR was explicitly requested; under AUTO a
        document that happens to have a sparse page should not nag about an
        optional dependency the user never asked for.
        """
        if self._ocr_warning_emitted:
            return
        self._ocr_warning_emitted = True
        message = (
            "OCR was requested but no backend is installed. "
            "Install it with: pip install trueparse[ocr]"
        )
        if self.options.ocr == OCRMode.ALWAYS:
            logger.warning(message)
        else:
            logger.debug(message)

    def _ocr_page(
        self, page: fitz.Page, page_num: int
    ) -> tuple[list[RawTextBlock], OCRPageResult]:
        """Rasterises a page and converts recognised lines into raw text blocks."""
        try:
            image_bytes = PDFRenderer.render_page_to_bytes(page, dpi=self.profile.render_dpi)
        except Exception as exc:
            logger.error(f"[Page {page_num:02d}] OCR render failed: {exc}")
            return [], OCRPageResult()

        scale = self.profile.render_dpi / 72.0
        result = OCREngine.recognize(image_bytes, scale=scale, origin=(0.0, 0.0))
        if not result.lines:
            return [], result

        blocks: list[RawTextBlock] = []
        for text, bbox, confidence in OCREngine.group_into_blocks(result):
            line_count = text.count("\n") + 1
            # Font size is not recoverable from a raster; approximate it from
            # line height so the heading ladder still sees a usable signal.
            approx_size = round((bbox.height / max(1, line_count)) * 0.72, 1)
            blocks.append(
                RawTextBlock(
                    bbox=bbox,
                    text=text,
                    spans=[],
                    avg_font_size=max(4.0, approx_size),
                    is_bold=False,
                    page_number=page_num,
                    char_count=len(text.replace("\n", "")),
                    line_count=line_count,
                    fonts=[f"ocr:{result.engine}"],
                    # Rides along so the classified element can inherit it.
                    ocr_confidence=confidence,
                )
            )

        return blocks, result

    def _build_pages(
        self,
        doc: fitz.Document,
        doc_id: str,
        inspection,
        scans: list[_PageScan],
        font_profile,
        public_assets: dict[str, Asset],
        dirs: dict[str, Path],
        progress_callback: Callable[[int, int, str], None] | None,
    ) -> tuple[list[Page], list[list[GenericElement]]]:
        pages_models: list[Page] = []
        pages_raw_elements: list[list[GenericElement]] = []
        total_pages = len(scans)

        # SHA-256 of every rendered vector crop, so a logo drawn on 40 pages is
        # rasterised and stored once rather than forty times.
        vector_hash_to_asset: dict[str, str] = {}

        for scan in scans:
            page_num = scan.page_number
            page = doc[page_num - 1]
            forensics = inspection.pages[page_num - 1]

            page_elements: list[GenericElement] = []
            page_asset_ids: list[str] = []
            table_elements = scan.tables

            # (A) Classify native/OCR text, excluding anything inside a table.
            non_table_blocks = [
                b for b in scan.raw_blocks
                if not self._inside_any(b.bbox, table_elements, ratio=0.5)
            ]
            text_elements = NativeExtractor.classify_and_build_elements(
                raw_blocks=non_table_blocks,
                body_font_size=scan.page_body_font,
                page_height=forensics.height,
                page_width=forensics.width,
                font_profile=font_profile,
                detect_formulas=self.options.extract_formulas,
            )
            if scan.ocr_applied:
                self._tag_ocr_provenance(text_elements, non_table_blocks)

            page_elements.extend(text_elements)
            page_elements.extend(table_elements)

            # (B) Place embedded raster images that are not inside a table.
            fig_counter = 1
            for asset_id, asset_model in public_assets.items():
                if asset_model.type != AssetType.IMAGE:
                    continue
                for occ in asset_model.occurrences:
                    if occ.page != page_num:
                        continue
                    if self._inside_any(occ.bbox, table_elements, ratio=0.5):
                        continue

                    page_asset_ids.append(asset_id)
                    page_elements.append(
                        FigureElement(
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
                    )
                    fig_counter += 1

            # (C) Vector drawing clusters, rendered as region crops.
            if self.options.extract_charts:
                fig_counter = self._extract_vector_figures(
                    page=page,
                    page_num=page_num,
                    doc_id=doc_id,
                    table_elements=table_elements,
                    public_assets=public_assets,
                    page_asset_ids=page_asset_ids,
                    page_elements=page_elements,
                    fig_counter=fig_counter,
                    vector_hash_to_asset=vector_hash_to_asset,
                )

            # (D) Debug page rendering
            if self.options.debug:
                debug_page_file = dirs["debug_pages"] / f"page_{page_num:04d}.png"
                PDFRenderer.render_page_to_file(
                    page, debug_page_file, dpi=self.profile.render_dpi
                )

            # (E) Reading order
            ordered_elements = ReadingOrderEngine.order_page_elements(
                elements=page_elements,
                page_width=forensics.width,
                page_height=forensics.height,
            )
            pages_raw_elements.append(ordered_elements)

            pages_models.append(
                Page(
                    page_number=page_num,
                    width=forensics.width,
                    height=forensics.height,
                    rotation=forensics.rotation,
                    elements=ordered_elements,
                    asset_ids=sorted(set(page_asset_ids)),
                )
            )

            logger.info(
                f"[Page {page_num:02d}/{total_pages:02d}] Extracted {len(text_elements)} text blocks, "
                f"{len(table_elements)} tables, {len(page_asset_ids)} assets "
                f"(Words: {forensics.word_count}{', OCR' if scan.ocr_applied else ''})"
            )
            if progress_callback:
                progress_callback(page_num, total_pages, f"page_{page_num}")

        return pages_models, pages_raw_elements

    def _extract_vector_figures(
        self,
        page: fitz.Page,
        page_num: int,
        doc_id: str,
        table_elements: list,
        public_assets: dict[str, Asset],
        page_asset_ids: list[str],
        page_elements: list[GenericElement],
        fig_counter: int,
        vector_hash_to_asset: dict[str, str],
    ) -> int:
        drawings_info = DrawingInspector.inspect_page(page, page_num)

        for cluster_bbox in drawings_info.clusters:
            # Skip clusters that are just a table's own ruling.
            if self._inside_any(cluster_bbox, table_elements, ratio=0.8):
                continue

            try:
                crop_bytes = PDFRenderer.render_region_to_bytes(
                    page=page, bbox=cluster_bbox, dpi=self.profile.render_dpi, format="PNG"
                )
            except Exception as exc:
                logger.warning(f"[Page {page_num:02d}] Vector crop render failed: {exc}")
                continue

            crop_sha = hashlib.sha256(crop_bytes).hexdigest()
            existing_id = vector_hash_to_asset.get(crop_sha)

            if existing_id is not None:
                # Same graphic already stored: record another occurrence only.
                crop_asset = public_assets[existing_id]
                crop_asset.occurrences.append(
                    AssetOccurrence(page=page_num, bbox=cluster_bbox)
                )
                crop_asset_id = existing_id
                crop_rel_path = crop_asset.path
            else:
                vec_fig_idx = len(vector_hash_to_asset) + 1
                crop_asset_id = f"asset_fig_{vec_fig_idx:04d}"
                crop_rel_path = f"assets/figures/{crop_asset_id}.png"

                self.storage.save_image_asset(
                    ExtractedImageAsset(
                        asset_id=crop_asset_id,
                        sha256=crop_sha,
                        image_bytes=crop_bytes,
                        ext="png",
                        mime_type="image/png",
                        width=int(cluster_bbox.width),
                        height=int(cluster_bbox.height),
                        occurrences=[],
                    ),
                    doc_id,
                    subfolder="figures",
                )

                public_assets[crop_asset_id] = Asset(
                    id=crop_asset_id,
                    type=AssetType.FIGURE,
                    path=crop_rel_path,
                    mime_type="image/png",
                    sha256=crop_sha,
                    width=int(cluster_bbox.width),
                    height=int(cluster_bbox.height),
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
            )
            fig_counter += 1

        return fig_counter

    @staticmethod
    def _inside_any(bbox: BoundingBox, tables: list, ratio: float) -> bool:
        """True when ``bbox`` sits mostly within one of ``tables``."""
        if not tables:
            return False
        rect = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
        area = rect.get_area()
        if area <= 0:
            return False
        for table in tables:
            t_rect = fitz.Rect(table.bbox.x0, table.bbox.y0, table.bbox.x1, table.bbox.y1)
            if (rect & t_rect).get_area() > ratio * area:
                return True
        return False

    @staticmethod
    def _tag_ocr_provenance(
        elements: list[DocumentElement],
        blocks: list[RawTextBlock],
    ) -> None:
        """Marks elements built from OCR blocks with OCR provenance.

        Elements and blocks are produced in lockstep by
        ``classify_and_build_elements``, so they can be paired positionally.
        """
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

    def _write_outputs(self, document: Document, doc_id: str, dirs: dict[str, Path]) -> None:
        output_dir = dirs["output"]

        json_file = self.storage.save_document_json(
            JSONSerializer.serialize(document), doc_id
        )
        logger.info(f"Serialization: document.json saved to {json_file}")

        md_file = self.storage.save_text(
            MarkdownExporter.export(document), output_dir, "document.md"
        )
        logger.info(f"Serialization: document.md saved to {md_file}")

        if self.options.emit_html:
            html_file = self.storage.save_text(
                HTMLExporter.export(document), output_dir, "document.html"
            )
            logger.info(f"Serialization: document.html saved to {html_file}")

        if self.options.emit_text:
            txt_file = self.storage.save_text(
                TextExporter.export(document), output_dir, "document.txt"
            )
            logger.info(f"Serialization: document.txt saved to {txt_file}")

        if self.options.emit_chunks:
            chunks = DocumentChunker.chunk(
                document,
                strategy=self.options.chunk_strategy,
                max_tokens=self.options.chunk_max_tokens,
                overlap_tokens=self.options.chunk_overlap_tokens,
            )
            chunks_file = self.storage.save_text(
                DocumentChunker.to_jsonl(chunks) + "\n", output_dir, "chunks.jsonl"
            )
            logger.info(
                f"Serialization: {len(chunks)} chunks "
                f"(strategy={self.options.chunk_strategy.value}) saved to {chunks_file}"
            )

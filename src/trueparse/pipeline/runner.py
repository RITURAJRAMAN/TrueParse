from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf as fitz  # PyMuPDF

from trueparse.config.options import EngineConfig, ParseOptions
from trueparse.config.profiles import ProfileSettings
from trueparse.config.profiles import resolve as resolve_profile
from trueparse.contracts.document import Issue, RepresentationReport, Scope, extraction_status
from trueparse.contracts.response import ParsedOutputData
from trueparse.exceptions import PDFEngineError
from trueparse.exporters.files import serialize_outputs
from trueparse.exporters.renderer import OutputRenderer
from trueparse.extraction.pdf.images import ExtractedImageAsset, ImageExtractor
from trueparse.extraction.pdf.inspector import PDFInspector
from trueparse.io.filesystem import FileSystemStorage
from trueparse.models.assets import Asset
from trueparse.models.document import Document
from trueparse.models.elements import FigureElement
from trueparse.models.enums import (
    ElementType,
    ErrorCode,
    ImageInclusionMode,
    OutputFormat,
)
from trueparse.models.quality import PageQuality
from trueparse.pipeline.stages.layout import build_pages
from trueparse.pipeline.stages.scan import scan_pages
from trueparse.processing.graph import DocumentGraphBuilder
from trueparse.processing.hierarchy import HierarchyEngine
from trueparse.processing.quality import QualityEngine
from trueparse.processing.tables import TableMerger
from trueparse.processing.text_merge import merge_paragraphs
from trueparse.processing.typography import TypographyAnalyzer
from trueparse.runtime.budget import WorkBudget
from trueparse.runtime.timing import StageTimings

logger = logging.getLogger("trueparse")

if TYPE_CHECKING:
    from trueparse.sdk.result import ParseResult


class PDFParser:
    """Main TrueParse orchestrator."""

    def __init__(
        self,
        options: ParseOptions | None = None,
        config: EngineConfig | None = None,
    ):
        self.options = (options or ParseOptions()).model_copy(deep=True)
        self.config = config or EngineConfig()

        self.profile: ProfileSettings = resolve_profile(self.options.profile)
        if self.options.render_dpi:
            self.profile.render_dpi = self.options.render_dpi
        output_root = self.options.output_path or self.config.default_output_root
        self.storage = FileSystemStorage(root_path=output_root)
        self._ocr_warning_emitted = False
        self.timings = StageTimings()
        self._issues: list[Issue] = []
        self._representation_reports: dict[str, RepresentationReport] = {}
        self.budget = WorkBudget(self.options.limits)

    def _timed(self, stage, function, *args, **kwargs):
        with self.timings.measure(stage):
            return function(*args, **kwargs)

    def parse(
        self,
        file_path: str | Path | BytesIO,
        original_filename: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> Document:
        self._last_output_data: ParsedOutputData | None = None
        self._page_warnings: dict[int, list[str]] = {}
        self._issues = []
        self._representation_reports = {}
        self._ocr_warning_emitted = False
        self.budget = WorkBudget(self.options.limits)
        self.timings = StageTimings()
        start_time = time.monotonic()
        path = file_path if isinstance(file_path, BytesIO) else Path(file_path).resolve()
        source_name = original_filename or (path.name if isinstance(path, Path) else "upload.pdf")
        stem = Path(source_name).stem or "document"
        logger.info(f"=== Starting PDF parsing for: {source_name} ===")
        logger.info(
            f"Profile '{self.options.profile.value}': dpi={self.profile.render_dpi} "
            f"tables={'+'.join(self.profile.table_strategies)} "
            f"spans={self.profile.detect_table_spans} ocr={self.options.ocr.value}"
        )

        if progress_callback:
            progress_callback(0, 1, "forensics")

        # 1. Intake & Forensics
        inspection = self._timed(
            "inspection",
            PDFInspector.inspect,
            file_path=path,
            max_file_size_mb=self.options.max_file_size_mb,
            password=self.options.password,
            max_pages=self.options.max_pages,
            check_deadline=lambda: self._check_timeout(start_time, "inspection"),
        )
        doc_id = inspection.document_id
        logger.info(
            f"Forensics: Document ID={doc_id} | Pages={inspection.page_count} | "
            f"Size={inspection.file_size_bytes / (1024 * 1024):.2f}MB | "
            f"NativeText={inspection.overall_native_text} | LikelyScan={inspection.overall_likely_scan}"
        )

        doc = (
            fitz.open(stream=path.getvalue(), filetype="pdf")
            if isinstance(path, BytesIO)
            else fitz.open(path)
        )
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
                source_name=source_name,
                stem=stem,
                start_time=start_time,
                progress_callback=progress_callback,
            )
        finally:
            doc.close()

    def parse_to_response(
        self,
        file_path: str | Path | BytesIO,
        original_filename: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> ParseResult:
        """Parses document and returns a typed ParseResponse according to output_config."""
        start_time = time.monotonic()
        doc = self.parse(file_path, original_filename, progress_callback)

        output_data = self._last_output_data or self._generate_outputs(doc)
        self._check_timeout(start_time, "response_serialization")

        from trueparse.pipeline.result import build_result

        return build_result(self, doc, output_data, start_time)

    def _generate_outputs(self, doc: Document) -> ParsedOutputData:
        renderer = OutputRenderer(self.options, self.timings)
        output = renderer.render(doc)
        self._representation_reports = renderer.reports
        return output

    def _check_timeout(self, start_time: float, current_stage: str) -> None:
        """Enforces timeout_seconds safeguard cleanly if configured."""
        if self.options.timeout_seconds and self.options.timeout_seconds > 0:
            elapsed = time.monotonic() - start_time
            if elapsed > self.options.timeout_seconds:
                raise PDFEngineError(
                    code=ErrorCode.PDF_RESOURCE_LIMIT,
                    message=f"Document parsing exceeded timeout ceiling ({self.options.timeout_seconds}s) during {current_stage}.",
                )

    def _parse_open_document(
        self,
        doc: fitz.Document,
        doc_id: str,
        inspection,
        source_name: str,
        stem: str,
        start_time: float,
        progress_callback: Callable[[int, int, str], None] | None,
    ) -> Document:
        total_pages = len(doc)
        if self.options.max_pages and self.options.max_pages > 0:
            total_pages = min(total_pages, self.options.max_pages)
            logger.info(f"Page limit applied: processing first {total_pages} of {len(doc)} pages")

        if progress_callback:
            progress_callback(0, total_pages, "extracting_assets")

        # 2. Extract embedded raster images
        extracted_assets_map: dict[str, tuple[ExtractedImageAsset, Asset]] = {}
        raw_asset_bytes: dict[str, bytes] = {}
        save_to_disk = self.options.output_config.save_to_disk
        img_mode = self.options.output_config.json_options.images
        asset_warnings: list[str] = []
        self._asset_warnings = asset_warnings
        formats = self.options.output_config.formats
        self._deliver_visual_bytes = img_mode != ImageInclusionMode.NONE and any(
            fmt in formats for fmt in (OutputFormat.JSON, OutputFormat.MARKDOWN, OutputFormat.HTML)
        )
        if not self._deliver_visual_bytes:
            img_mode = ImageInclusionMode.NONE

        if self.options.extract_images:
            try:
                extracted_assets_map = self._timed(
                    "asset_extraction",
                    ImageExtractor.extract_embedded_images,
                    doc,
                    relative_asset_dir="assets/images",
                    max_pages=total_pages,
                    check_deadline=lambda: self._check_timeout(start_time, "asset_extraction"),
                    warnings=asset_warnings,
                    include_bytes=img_mode != ImageInclusionMode.NONE,
                    max_asset_bytes=self.options.limits.max_asset_bytes,
                    max_image_pixels=self.options.limits.max_render_pixels,
                )
                for extracted_img, _asset_obj in extracted_assets_map.values():
                    if extracted_img.image_bytes:
                        raw_asset_bytes[extracted_img.asset_id] = extracted_img.image_bytes
                    if extracted_img.image_bytes and save_to_disk and img_mode == ImageInclusionMode.PATH:
                        self.storage.save_image_asset(
                            extracted=extracted_img, document_id=doc_id, subfolder="images"
                        )
                logger.info(
                    f"Asset Extraction: Found {len(extracted_assets_map)} unique embedded "
                    "raster images (deduplicated by SHA-256)"
                )
            except Exception as exc:
                if isinstance(exc, PDFEngineError):
                    raise
                asset_warnings.append(f"Embedded image extraction failed: {exc}")
                logger.warning(f"Fail-soft: Embedded image extraction encountered warning: {exc}")

        self._raw_asset_bytes = raw_asset_bytes
        self.budget.reserve_asset(sum(len(value) for value in raw_asset_bytes.values()))
        public_assets: dict[str, Asset] = {k: v[1] for k, v in extracted_assets_map.items()}

        self._check_timeout(start_time, "asset_extraction")

        # 3. First pass: read text and tables from every page with fail-soft resilience.
        scans = scan_pages(self, doc, inspection, total_pages, start_time, progress_callback)

        self._check_timeout(start_time, "page_scanning")

        # 4. Document-wide typography
        all_blocks = [b for scan in scans for b in scan.raw_blocks]
        font_profile = self._timed("typography", TypographyAnalyzer.build_font_profile, all_blocks)
        logger.info(
            f"Typography: body={font_profile.body_size}pt "
            f"heading_ladder={font_profile.ladder or 'none detected'}"
        )

        # 5. Second pass: classify, place assets, order, and score with fail-soft per page.
        pages_models, pages_raw_elements = build_pages(
            self,
            doc=doc,
            doc_id=doc_id,
            inspection=inspection,
            scans=scans,
            font_profile=font_profile,
            public_assets=public_assets,
            raw_asset_bytes=raw_asset_bytes,
            save_to_disk=save_to_disk,
            progress_callback=progress_callback,
            start_time=start_time,
        )

        self._check_timeout(start_time, "building_pages")
        if (
            scans
            and all(scan.scan_failed for scan in scans)
            and not any(
                e.type not in (ElementType.FIGURE, ElementType.IMAGE, ElementType.DIAGRAM, ElementType.CHART)
                for page_elements in pages_raw_elements
                for e in page_elements
            )
            and not raw_asset_bytes
        ):
            raise PDFEngineError(
                ErrorCode.PDF_PARSE_ERROR,
                "Every requested page failed extraction.",
                document_id=doc_id,
                details={
                    "warnings": [scan.scan_error for scan in scans if scan.scan_error],
                    "stage_timings_ms": self.timings.snapshot(),
                },
            )

        if progress_callback:
            progress_callback(total_pages, total_pages, "building_hierarchy")

        # 6. Rejoin prose split across columns and page breaks.
        if self.profile.merge_paragraphs:
            before = sum(len(p) for p in pages_raw_elements)
            pages_raw_elements = self._timed("paragraph_healing", merge_paragraphs, pages_raw_elements)
            after = sum(len(p) for p in pages_raw_elements)
            if before != after:
                logger.info(f"Layout: merged {before - after} split paragraph fragments")

        # 7. Join tables that continue onto the following page.
        if self.options.extract_tables and self.profile.merge_cross_page_tables:
            before = sum(1 for page in pages_raw_elements for e in page if e.type == ElementType.TABLE)
            pages_raw_elements = self._timed(
                "table_continuation", TableMerger.merge_cross_page_tables, pages_raw_elements
            )
            after = sum(1 for page in pages_raw_elements for e in page if e.type == ElementType.TABLE)
            if before != after:
                logger.info(f"Tables: merged {before - after} cross-page table continuations")

        # 8. Sections, heading hierarchy & caption associations
        formats = self.options.output_config.formats
        json_opts = self.options.output_config.json_options
        needs_hierarchy = OutputFormat.CHUNKS in formats or (
            OutputFormat.JSON in formats and (json_opts.hierarchy or json_opts.relationships)
        )
        if needs_hierarchy:
            sections, updated_pages_elements = self._timed(
                "hierarchy", HierarchyEngine.build_sections_and_captions, pages_raw_elements
            )
        else:
            sections, updated_pages_elements = [], pages_raw_elements
        for p_idx, page_obj in enumerate(pages_models):
            page_obj.elements = updated_pages_elements[p_idx]

        # 9. Relational document graph
        relationships = []
        if OutputFormat.JSON in formats and json_opts.relationships:
            relationships = self._timed(
                "relationships",
                DocumentGraphBuilder.build_relationships,
                sections=sections,
                pages_elements=updated_pages_elements,
            )

        # 10. Re-score pages now that elements are final, then score the document.
        for page_obj, scan in zip(pages_models, scans, strict=True):
            if scan.scan_failed:
                page_obj.quality = PageQuality(
                    text_confidence=0.0,
                    layout_confidence=0.0,
                    unknown_ratio=1.0,
                    ocr_applied=scan.ocr_applied,
                    ocr_confidence=scan.ocr_confidence,
                    warnings=[
                        scan.scan_error or "Page scanning failed during extraction.",
                        *scan.warnings,
                        *self._page_warnings.get(page_obj.page_number, []),
                    ],
                )
                continue

            forensics = inspection.pages[page_obj.page_number - 1]
            page_obj.quality = self._timed(
                "quality",
                QualityEngine.evaluate_page,
                page_num=page_obj.page_number,
                text_elements_count=sum(
                    1 for e in page_obj.elements if e.type not in (ElementType.TABLE, ElementType.FIGURE)
                ),
                has_native_text=forensics.has_native_text,
                likely_scan=forensics.likely_scan,
                elements=page_obj.elements,
                page_width=page_obj.width,
                page_height=page_obj.height,
                ocr_applied=scan.ocr_applied,
                ocr_confidence=scan.ocr_confidence,
            )
            page_obj.quality.warnings.extend(scan.warnings)
            page_obj.quality.warnings.extend(self._page_warnings.get(page_obj.page_number, []))

        doc_quality = self._timed("quality", QualityEngine.evaluate_document, pages_models)
        doc_quality.warnings.extend(asset_warnings)
        self._issues.extend(
            Issue(code="ASSET_EXTRACTION_INCOMPLETE", stage="assets", message=message)
            for message in asset_warnings
        )

        # 11. Construct Document
        final_doc = Document(
            id=doc_id,
            engine_version=self.config.engine_version,
            source_file=source_name,
            metadata=inspection.metadata,
            pages=pages_models,
            sections=sections,
            assets=public_assets,
            relationships=relationships,
            quality=doc_quality,
            warnings=doc_quality.warnings,
            issues=self._issues,
            scope=Scope(
                source_pages=len(doc),
                requested_pages=list(range(1, total_pages + 1)),
                processed_pages=[s.page_number for s in scans if not s.scan_failed],
                failed_pages=[s.page_number for s in scans if s.scan_failed],
            ),
            extraction_config=self.options.model_dump(
                mode="json", exclude={"password", "output_path", "output_config", "debug"}
            ),
        )

        if img_mode == ImageInclusionMode.BASE64:
            for asset_id, asset in public_assets.items():
                asset.path = None
                if asset_id in raw_asset_bytes:
                    encoded = base64.b64encode(raw_asset_bytes[asset_id]).decode("ascii")
                    asset.data_uri = f"data:{asset.mime_type};base64,{encoded}"
            for page_model in final_doc.pages:
                for element in page_model.elements:
                    if isinstance(element, FigureElement) and element.asset_id in public_assets:
                        element.asset_path = public_assets[element.asset_id].data_uri

        # 12. Serialize requested deliverables to disk if save_to_disk is enabled
        self._check_timeout(start_time, "serialization")
        if (
            self.options.strict
            and extraction_status(final_doc.issues, final_doc.scope.failed_pages) == "partial"
        ):
            raise PDFEngineError(
                ErrorCode.EXTRACTION_INCOMPLETE,
                "Strict parsing rejected incomplete extraction.",
                document_id=doc_id,
                details={"issues": [issue.model_dump(mode="json") for issue in final_doc.issues]},
            )
        if save_to_disk:
            self._write_outputs(final_doc, doc_id, stem)
        self._check_timeout(start_time, "completion")

        elapsed = time.monotonic() - start_time
        logger.info(
            f"=== Parse Completed: Document ID={doc_id} | Pages={len(pages_models)} | "
            f"Assets={len(public_assets)} | Sections={len(sections)} | "
            f"Quality={doc_quality.overall_score:.2f} | Time={elapsed:.2f}s ==="
        )
        return final_doc

    def _write_outputs(
        self,
        document: Document,
        doc_id: str,
        stem: str,
    ) -> None:
        """Writes deliverables directly to doc_dir according to output_config.formats."""
        output = self._generate_outputs(document)
        self._last_output_data = output
        for extension, content in serialize_outputs(output, self.options.output_config.formats):
            self.storage.save_deliverable(content, doc_id, f"{stem}.{extension}")

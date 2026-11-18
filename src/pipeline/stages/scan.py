from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from trueparse.contracts.document import Issue
from trueparse.exceptions import PDFEngineError
from trueparse.extraction.pdf.text import NativeExtractor
from trueparse.extraction.tables import NativeTableExtractor
from trueparse.models.enums import (
    ErrorCode,
    OCRMode,
)
from trueparse.pipeline.context import PageScan
from trueparse.pipeline.stages.ocr import ocr_page, should_ocr

if TYPE_CHECKING:
    from trueparse.pipeline.runner import PDFParser

logger = logging.getLogger("trueparse")


def scan_pages(
    parser: PDFParser, doc, inspection, total_pages, start_time, progress_callback
) -> list[PageScan]:
    from trueparse.extraction.ocr.recovery import missing_regions, usable_text

    scans: list[PageScan] = []
    for page_idx in range(total_pages):
        parser._check_timeout(start_time, f"scanning_page_{page_idx + 1}")
        page_num = page_idx + 1
        forensics = inspection.pages[page_idx]
        scan = PageScan(page_number=page_num)
        scan.warnings.extend(forensics.warnings)
        parser._issues.extend(
            Issue(code="PAGE_INSPECTION_INCOMPLETE", stage="inspection", page=page_num, message=message)
            for message in forensics.warnings
        )
        try:
            page = doc[page_idx]
            if not forensics.width or not forensics.height:
                forensics.width, forensics.height = page.rect.width, page.rect.height
        except Exception as exc:
            if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                raise
            scan.scan_failed = True
            scan.scan_error = f"Page {page_num} loading failed: {exc}"
            parser._issues.append(
                Issue(code="PAGE_LOAD_FAILED", stage="inspection", page=page_num, message=scan.scan_error)
            )
            scans.append(scan)
            if progress_callback:
                progress_callback(page_num, total_pages, f"scanning_page_{page_num}")
            continue
        if parser.options.extract_tables:
            try:
                previous_warnings = len(scan.warnings)
                scan.tables = parser._timed(
                    "table_extraction",
                    NativeTableExtractor.extract_page_tables,
                    page=page,
                    page_number=page_num,
                    start_index=1,
                    strategies=parser.profile.table_strategies,
                    detect_spans=parser.profile.detect_table_spans,
                    warnings=scan.warnings,
                    render_markdown=False,
                    render_html=False,
                )
                parser._issues.extend(
                    Issue(code="TABLE_EXTRACTION_INCOMPLETE", stage="tables", page=page_num, message=message)
                    for message in scan.warnings[previous_warnings:]
                )
            except Exception as exc:
                if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                    raise
                scan.warnings.append(f"Page {page_num} table extraction failed: {exc}")
                parser._issues.append(
                    Issue(code="TABLE_EXTRACTION_FAILED", stage="tables", page=page_num, message=str(exc))
                )
        try:
            scan.raw_blocks, scan.page_body_font = parser._timed(
                "native_text", NativeExtractor.extract_page_text_blocks, page=page, page_number=page_num
            )
        except Exception as exc:
            if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                raise
            scan.scan_failed = True
            scan.scan_error = f"Page {page_num} text extraction failed: {exc}"
        if should_ocr(parser, forensics, scan.raw_blocks):
            try:
                regions = missing_regions(
                    page,
                    scan.raw_blocks,
                    forensics,
                    always=parser.options.ocr == OCRMode.ALWAYS or scan.scan_failed,
                    min_chars=parser.profile.ocr_min_chars,
                )
            except Exception as exc:
                if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                    raise
                scan.warnings.append(f"Page {page_num} OCR region inspection failed: {exc}")
                regions = [None]
            confidences = []
            for region in regions:
                try:
                    recovered, result = ocr_page(parser, page, page_num, region, scan.raw_blocks)
                    if result.error:
                        raise RuntimeError(result.error)
                    if result.lines:
                        scan.ocr_applied = True
                        confidences.append(result.mean_confidence)
                    if recovered:
                        scan.raw_blocks = [
                            block for block in scan.raw_blocks if usable_text(block.text)
                        ] + recovered
                        scan.scan_failed = False
                        if scan.scan_error:
                            scan.warnings.append(scan.scan_error + " (recovered with OCR)")
                            scan.scan_error = None
                    elif not result.lines:
                        supplemental = (
                            region is not None
                            and not scan.scan_failed
                            and not forensics.likely_scan
                            and any(usable_text(block.text) for block in scan.raw_blocks)
                        )
                        scan.warnings.append(f"Page {page_num}: OCR returned no readable text.")
                        parser._issues.append(
                            Issue(
                                code="OCR_NO_TEXT",
                                stage="ocr",
                                page=page_num,
                                message="OCR returned no readable text.",
                                severity="info" if supplemental else "warning",
                            )
                        )
                except Exception as exc:
                    if isinstance(exc, PDFEngineError) and exc.code == ErrorCode.PDF_RESOURCE_LIMIT:
                        raise
                    scan.warnings.append(f"Page {page_num} OCR recovery failed: {exc}")
                    parser._issues.append(
                        Issue(code="OCR_FAILED", stage="ocr", page=page_num, message=str(exc))
                    )
            if confidences:
                scan.ocr_confidence = sum(confidences) / len(confidences)
        if scan.scan_failed:
            parser._issues.append(
                Issue(
                    code="TEXT_EXTRACTION_FAILED",
                    stage="native_text",
                    page=page_num,
                    message=scan.scan_error or "Native extraction failed.",
                )
            )
        if forensics.likely_scan and not scan.raw_blocks and not scan.tables:
            parser._issues.append(
                Issue(
                    code="TEXT_UNAVAILABLE",
                    stage="ocr",
                    page=page_num,
                    message="Image content has no recovered text.",
                )
            )
        scans.append(scan)
        if progress_callback:
            progress_callback(page_num, total_pages, f"scanning_page_{page_num}")
    return scans

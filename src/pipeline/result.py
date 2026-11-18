from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from trueparse.contracts.document import extraction_status
from trueparse.exceptions import PDFEngineError
from trueparse.extraction.ocr.engine import ocr_available
from trueparse.models.enums import ErrorCode, ImageInclusionMode, OCRMode

if TYPE_CHECKING:
    from trueparse.contracts.response import ParsedOutputData
    from trueparse.models.document import Document
    from trueparse.pipeline.runner import PDFParser
    from trueparse.sdk.result import ParseResult


def build_result(
    parser: PDFParser, document: Document, output: ParsedOutputData, started: float
) -> ParseResult:
    from trueparse.sdk.result import ParseResult

    warnings = list(document.warnings)
    needs_ocr = parser.options.ocr == OCRMode.ALWAYS or any(
        "scanned image" in warning for warning in warnings
    )
    if parser.options.ocr != OCRMode.NEVER and needs_ocr and not ocr_available():
        warnings.append("Local OCR is unavailable. Install trueparse[ocr] to recognize scanned pages.")
    status = extraction_status(document.issues, document.scope.failed_pages)
    result = ParseResult(
        document_id=document.id,
        source_file=document.source_file or "document.pdf",
        status=status,
        formats_provided=parser.options.output_config.formats,
        data=output,
        warnings=list(dict.fromkeys(warnings)),
        issues=document.issues,
        scope=document.scope,
        representations=parser._representation_reports,
        metrics={
            "duration_ms": int((time.monotonic() - started) * 1000),
            "page_count": len(document.pages),
            "asset_count": len(document.assets or {}),
            "resource_usage": {
                "rendered_pixels": parser.budget.rendered_pixels,
                "ocr_pages": len(parser.budget.ocr_pages),
                "asset_bytes": parser.budget.asset_bytes,
            },
            "stage_timings_ms": parser.timings.snapshot(),
            "stage_calls": parser.timings.calls(),
        },
    )
    result._asset_bytes = dict(parser._raw_asset_bytes)
    if parser.options.output_config.json_options.images == ImageInclusionMode.PATH:
        result._asset_paths = {
            key: asset.path for key, asset in (document.assets or {}).items() if asset.path
        }
    original_metrics_size = len(
        json.dumps(result.metrics, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    with parser.timings.measure("response_validation"):
        size = 0
        for piece in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(
            result.model_dump(mode="json")
        ):
            size += len(piece.encode("utf-8"))
            if size > parser.options.limits.max_response_bytes:
                raise PDFEngineError(
                    ErrorCode.PDF_RESOURCE_LIMIT,
                    "Response exceeds max_response_bytes.",
                    document_id=document.id,
                )
    result.metrics.update(
        duration_ms=int((time.monotonic() - started) * 1000),
        stage_timings_ms=parser.timings.snapshot(),
        stage_calls=parser.timings.calls(),
    )
    final_metrics_size = len(
        json.dumps(result.metrics, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if size + final_metrics_size - original_metrics_size > parser.options.limits.max_response_bytes:
        raise PDFEngineError(
            ErrorCode.PDF_RESOURCE_LIMIT, "Response exceeds max_response_bytes.", document_id=document.id
        )
    result._response_bytes = size + final_metrics_size - original_metrics_size
    return result

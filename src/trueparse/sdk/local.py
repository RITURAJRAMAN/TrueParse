from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from trueparse.chunking.tokenization import TokenizerName
from trueparse.config.options import (
    ResourceLimits,
)
from trueparse.contracts.request import JsonProjection, ParseRequest
from trueparse.exceptions import PDFEngineError
from trueparse.io.sources import Source
from trueparse.models.enums import (
    ChunkStrategy,
    ErrorCode,
    ImageInclusionMode,
    OCRMode,
    OutputFormat,
    ParsingProfile,
)
from trueparse.runtime.execution import _parse_request
from trueparse.sdk.result import ParseResult


def parse(
    source: Source,
    *,
    formats: Sequence[OutputFormat | str] = ("markdown",),
    images: ImageInclusionMode | str = "none",
    profile: ParsingProfile | str = "balanced",
    ocr: OCRMode | str = "auto",
    filename: str | None = None,
    password: str | None = None,
    max_pages: int | None = None,
    timeout_seconds: float | None = None,
    chunk_strategy: ChunkStrategy | str = "hybrid",
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    chunk_tokenizer: TokenizerName = "approximate",
    json_options: JsonProjection | dict[str, Any] | None = None,
    strict: bool = False,
    limits: ResourceLimits | dict[str, int] | None = None,
) -> ParseResult:
    """Parse a local PDF in memory. Request additional formats only when needed.

    ``images`` controls visual delivery across exports; ``json_options`` affects
    only the document JSON. Use result.save(directory) for explicit persistence.
    A positive timeout runs parsing in a supervised process with a hard deadline.
    In standalone scripts, call parse() under an ``if __name__ == '__main__'`` guard.
    """
    try:
        request = ParseRequest.model_validate(
            {
                "strict": strict,
                "limits": limits or {},
                "formats": list(formats),
                "images": images,
                "profile": profile,
                "ocr": ocr,
                "password": password,
                "max_pages": max_pages,
                "timeout_seconds": timeout_seconds,
                "chunk_strategy": chunk_strategy,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "chunk_tokenizer": chunk_tokenizer,
                "json_options": json_options or {},
            }
        )
    except ValidationError as exc:
        raise PDFEngineError(ErrorCode.INVALID_OPTIONS, str(exc)) from exc
    return _parse_request(source, request, filename)


async def aparse(source: Source, **options: Any) -> ParseResult:
    """Async application helper with the same options and result as parse()."""
    return await asyncio.to_thread(parse, source, **options)

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from trueparse.exceptions import PDFEngineError
from trueparse.models.enums import (
    ErrorCode,
)

Source = str | Path | bytes | BinaryIO


_MAX_SOURCE_BYTES = 200 * 1024 * 1024


def _source_bytes(source: Source, filename: str | None) -> tuple[bytes, str]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():
            raise PDFEngineError(ErrorCode.INVALID_PDF, f"PDF file not found: {path}")
        with path.open("rb") as stream:
            content = stream.read(_MAX_SOURCE_BYTES + 1)
        filename = filename or path.name
    elif isinstance(source, bytes):
        content = source
    else:
        try:
            position = source.tell()
        except (AttributeError, OSError):
            position = None
        try:
            content = source.read(_MAX_SOURCE_BYTES + 1)
        finally:
            if position is not None:
                source.seek(position)
    if not isinstance(content, bytes):
        raise TypeError("Pass a PDF path, bytes, or a binary file object.")
    if len(content) > _MAX_SOURCE_BYTES:
        raise PDFEngineError(ErrorCode.PDF_RESOURCE_LIMIT, "PDF exceeds the 200 MB SDK input limit.")
    return content, Path((filename or "document.pdf").replace("\\", "/")).name

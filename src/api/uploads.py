from __future__ import annotations

from io import BytesIO

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from trueparse.api.policy import parse_options
from trueparse.api.settings import max_upload_bytes
from trueparse.contracts.request import ParseRequest
from trueparse.exceptions import PDFEngineError
from trueparse.models.enums import ErrorCode


class MemoryMultipartParser(MultiPartParser):
    # Attribute names used by current and earlier supported Starlette releases.
    spool_max_size: int
    max_file_size: int


async def _upload(request: Request) -> tuple[bytes, str, ParseRequest]:
    limit = max_upload_bytes()
    content_type = request.headers.get("content-type", "").lower()
    multipart = content_type.startswith("multipart/form-data")
    transport_limit = limit + (65536 if multipart else 0)

    async def bounded_stream():
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > transport_limit:
                raise PDFEngineError(ErrorCode.UPLOAD_TOO_LARGE, "Upload exceeds the configured size limit.")
            yield chunk

    if multipart:
        parser = MemoryMultipartParser(request.headers, bounded_stream(), max_files=1, max_fields=1)
        # Both attribute names are set for supported Starlette releases. Since
        # the whole request is bounded below this threshold, files never roll
        # over into OS temporary storage before our endpoint sees them.
        parser.spool_max_size = transport_limit + 1
        parser.max_file_size = transport_limit + 1
        form = None
        try:
            form = await parser.parse()
            if set(form) - {"file", "options"}:
                raise PDFEngineError(
                    ErrorCode.INVALID_OPTIONS, "Multipart fields are 'file' and optional JSON 'options' only."
                )
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise PDFEngineError(ErrorCode.INVALID_OPTIONS, "Provide one PDF in the 'file' field.")
            content = await upload.read(limit + 1)
            filename = upload.filename or "document.pdf"
            encoded = form.get("options", "{}")
            if not isinstance(encoded, str) or len(encoded) > 16384:
                raise PDFEngineError(
                    ErrorCode.INVALID_OPTIONS, "Options must be a JSON object of at most 16 KB."
                )
        except MultiPartException as exc:
            raise PDFEngineError(ErrorCode.INVALID_OPTIONS, str(exc)) from exc
        finally:
            if form is not None:
                await form.close()
            # Also close incomplete in-memory file parts after a size error.
            for file in parser._files_to_close_on_error:
                file.close()
    elif content_type.split(";", 1)[0] == "application/pdf":
        buffer = BytesIO()
        async for chunk in bounded_stream():
            buffer.write(chunk)
        content = buffer.getvalue()
        filename = request.headers.get("X-Filename", "document.pdf")
        encoded = request.headers.get("X-Parse-Options", "{}")
        if len(encoded) > 16384:
            raise PDFEngineError(ErrorCode.INVALID_OPTIONS, "Options exceed 16 KB.")
    else:
        raise HTTPException(status_code=415, detail="Use multipart/form-data or application/pdf.")
    if len(content) > limit:
        raise PDFEngineError(ErrorCode.UPLOAD_TOO_LARGE, "Upload exceeds the configured size limit.")
    options = parse_options(encoded)
    return content, filename, options

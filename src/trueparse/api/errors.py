from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from trueparse.exceptions import PDFEngineError
from trueparse.models.enums import ErrorCode


async def engine_error(_request: Request, exc: PDFEngineError):
    status = {
        ErrorCode.INVALID_PDF: 400,
        ErrorCode.INVALID_OPTIONS: 422,
        ErrorCode.PDF_PASSWORD_REQUIRED: 422,
        ErrorCode.PDF_PASSWORD_INCORRECT: 403,
        ErrorCode.PDF_RESOURCE_LIMIT: 413,
        ErrorCode.UPLOAD_TOO_LARGE: 413,
        ErrorCode.PARSE_TIMEOUT: 504,
    }.get(exc.code, 422)
    return JSONResponse(status_code=status, content=exc.to_dict())


async def http_error(_request: Request, exc: HTTPException):
    code = "AUTHENTICATION_FAILED" if exc.status_code == 401 else "INVALID_OPTIONS"
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": str(exc.detail)}},
        headers=exc.headers,
    )

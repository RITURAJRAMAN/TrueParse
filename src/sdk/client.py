"""Small HTTP client sharing the local SDK request and result models."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import ValidationError

from trueparse.contracts.request import ParseRequest
from trueparse.exceptions import PDFEngineError
from trueparse.io.sources import Source, _source_bytes
from trueparse.models.enums import ErrorCode, ImageInclusionMode
from trueparse.sdk.job_client import JobClientMethods
from trueparse.sdk.result import ParseResult


def _response_error(body: bytearray, status: int) -> PDFEngineError:
    try:
        detail = json.loads(body)["error"]
        code = ErrorCode(detail["code"])
        message = detail["message"]
        retryable = detail.get("retryable", False)
        details = detail.get("details", {})
        context = {key: detail.get(key) for key in ("request_id", "document_id", "element_id")}
        page = detail.get("page")
        if (
            not isinstance(message, str)
            or not isinstance(retryable, bool)
            or not isinstance(details, dict)
            or any(value is not None and not isinstance(value, str) for value in context.values())
            or (page is not None and (type(page) is not int or page < 1))
        ):
            raise ValueError("Invalid error fields")
        return PDFEngineError(
            code,
            message,
            retryable=retryable,
            details={**details, "http_status": status},
            page=page,
            **context,
        )
    except (ValueError, KeyError, AttributeError, TypeError):
        return PDFEngineError(
            ErrorCode.PDF_PARSE_ERROR,
            "TrueParse returned an invalid error response.",
            details={"http_status": status},
        )


class TrueParseClient(JobClientMethods):
    """Use a deployed TrueParse service without changing result-handling code."""

    def __init__(
        self, base_url: str = "http://127.0.0.1:8000", *, api_key: str | None = None, timeout: float = 130
    ):
        try:
            import httpx
        except ImportError as exc:
            raise ImportError("Install trueparse[client] to use TrueParseClient.") from exc
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers={"X-API-Key": api_key} if api_key else {}
        )
        self._transport_error = httpx.TransportError

    def parse(self, source: Source, *, filename: str | None = None, **options: Any) -> ParseResult:
        try:
            request = ParseRequest.model_validate(options)
        except ValidationError as exc:
            raise PDFEngineError(ErrorCode.INVALID_OPTIONS, str(exc)) from exc
        if request.images == ImageInclusionMode.PATH:
            raise PDFEngineError(
                ErrorCode.INVALID_OPTIONS,
                "HTTP responses support images='none' or 'base64'; use result.save() locally.",
            )
        content, name = _source_bytes(source, filename)
        try:
            with self._http.stream(
                "POST",
                "/v1/parse",
                files={"file": (name, content, "application/pdf")},
                data={"options": json.dumps(request.model_dump(mode="json", exclude_unset=True))},
            ) as response:
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(body) + len(chunk) > request.limits.max_response_bytes:
                        raise PDFEngineError(
                            ErrorCode.PDF_RESOURCE_LIMIT, "HTTP response exceeds max_response_bytes."
                        )
                    body.extend(chunk)
                if response.is_error:
                    raise _response_error(body, response.status_code)
                try:
                    return ParseResult.model_validate_json(body)
                except (ValidationError, ValueError) as exc:
                    raise PDFEngineError(
                        ErrorCode.SERIALIZATION_ERROR, "TrueParse returned an invalid result."
                    ) from exc
        except self._transport_error as exc:
            raise PDFEngineError(
                ErrorCode.SERVICE_UNAVAILABLE,
                f"TrueParse service could not be reached: {exc}",
                retryable=True,
            ) from exc

    async def aparse(self, source: Source, **options: Any) -> ParseResult:
        return await asyncio.to_thread(self.parse, source, **options)

    def _job_request(self, method, path, model, *, max_response_bytes=128 * 1024**2, **kwargs):
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        try:
            with self._http.stream(method, path, **kwargs) as response:
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(body) + len(chunk) > max_response_bytes:
                        raise PDFEngineError(
                            ErrorCode.PDF_RESOURCE_LIMIT, "HTTP response exceeds max_response_bytes."
                        )
                    body.extend(chunk)
                if response.is_error:
                    raise _response_error(body, response.status_code)
                try:
                    return model.model_validate_json(body)
                except ValueError as exc:
                    raise PDFEngineError(
                        ErrorCode.SERIALIZATION_ERROR, "TrueParse returned an invalid result."
                    ) from exc
        except self._transport_error as exc:
            raise PDFEngineError(
                ErrorCode.SERVICE_UNAVAILABLE, "TrueParse service could not be reached.", retryable=True
            ) from exc

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> TrueParseClient:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

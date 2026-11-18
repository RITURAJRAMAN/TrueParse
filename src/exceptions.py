from typing import Any

from trueparse.models.enums import ErrorCode


class PDFEngineError(Exception):
    """Base exception for TrueParse."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        request_id: str | None = None,
        document_id: str | None = None,
        page: int | None = None,
        element_id: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id
        self.document_id = document_id
        self.page = page
        self.element_id = element_id
        self.retryable = retryable
        self.details = details or {}

    def __reduce__(self):
        """Preserve constructor arguments across process-worker boundaries.

        Exception's default pickler only saves ``args`` (the message), but our
        constructor also requires an ErrorCode. Without this, a malformed PDF
        breaks the process pool when its exception is sent back to the parent.
        """
        return type(self), (self.code, self.message), self.__dict__

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "request_id": self.request_id,
                "document_id": self.document_id,
                "page": self.page,
                "element_id": self.element_id,
                "retryable": self.retryable,
                "details": self.details,
            }
        }

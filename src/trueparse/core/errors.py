from typing import Any

from trueparse.core.enums import ErrorCode


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

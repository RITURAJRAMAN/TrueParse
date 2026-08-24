from typing import Any, Optional
from trueparse.core.enums import ErrorCode


class PDFEngineError(Exception):
    """Base exception for TrueParse."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        request_id: Optional[str] = None,
        document_id: Optional[str] = None,
        page: Optional[int] = None,
        element_id: Optional[str] = None,
        retryable: bool = False,
        details: Optional[dict[str, Any]] = None,
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

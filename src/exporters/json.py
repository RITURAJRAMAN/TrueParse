from __future__ import annotations

from trueparse.config.options import JsonOptions
from trueparse.exceptions import PDFEngineError
from trueparse.exporters.projection import project_document
from trueparse.models.document import Document
from trueparse.models.enums import ErrorCode


class DocumentProjector:
    @staticmethod
    def project(document: Document, options: JsonOptions) -> dict:
        try:
            return project_document(document, options).model_dump(mode="json", exclude_none=True)
        except ValueError as exc:
            raise PDFEngineError(
                ErrorCode.SERIALIZATION_ERROR, f"Document contract validation failed: {exc}"
            ) from exc

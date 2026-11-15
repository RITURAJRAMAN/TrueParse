from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from trueparse.contracts.document import CanonicalDocument
from trueparse.models.enums import OutputFormat


class ParsedOutputData(BaseModel):
    """Container for requested format representations. Unrequested formats are None."""

    model_config = {"populate_by_name": True, "serialize_by_alias": True, "validate_assignment": True}

    document: dict[str, Any] | None = Field(
        default=None, alias="json", description="Canonical document graph"
    )
    markdown: str | None = Field(default=None, description="Clean Markdown export")
    html: str | None = Field(default=None, description="Semantic HTML export")
    text: str | None = Field(default=None, description="Plain reading-order text")
    chunks: list[dict[str, Any]] | None = Field(default=None, description="RAG chunks")

    @field_validator("document")
    @classmethod
    def validate_document(cls, value):
        if value is not None:
            CanonicalDocument.model_validate(value)
        return value


class ParseResponse(BaseModel):
    """Standardized response envelope returned by API and SDK."""

    document_id: str
    model_config = {"extra": "forbid"}
    status: Literal["completed", "partial"] = "completed"
    formats_provided: list[OutputFormat] = Field(default_factory=list)
    data: ParsedOutputData = Field(default_factory=ParsedOutputData)
    metrics: dict[str, Any] = Field(
        default_factory=dict, description="Execution metrics (duration_ms, page_count, asset_count)"
    )

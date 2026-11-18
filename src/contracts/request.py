from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trueparse.chunking.tokenization import TokenizerName
from trueparse.config.options import (
    ResourceLimits,
    TableOutputOptions,
)
from trueparse.models.enums import (
    ChunkStrategy,
    ImageInclusionMode,
    OCRMode,
    OutputFormat,
    ParsingProfile,
)


class TableProjection(TableOutputOptions):
    model_config = ConfigDict(extra="forbid")


class JsonProjection(BaseModel):
    """JSON-only field selection. Visual delivery is controlled by ``images``."""

    model_config = ConfigDict(extra="forbid")
    coordinates: bool = False
    metadata: bool = True
    hierarchy: bool = True
    quality_scores: bool = False
    relationships: bool = True
    text_only: bool = False
    tables: TableProjection = Field(default_factory=TableProjection)

    @field_validator("tables", mode="before")
    @classmethod
    def normalize_tables(cls, value):
        if isinstance(value, bool):
            return {} if value else {"grid": False, "markdown": False, "html": False}
        return value


class ParseRequest(BaseModel):
    """One request contract used by the SDK, HTTP service and CLI."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    strict: bool = False
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    formats: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.MARKDOWN], min_length=1)
    images: ImageInclusionMode = ImageInclusionMode.NONE
    profile: ParsingProfile = ParsingProfile.BALANCED
    ocr: OCRMode = OCRMode.AUTO
    password: str | None = None
    max_pages: int | None = Field(default=None, gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    chunk_strategy: ChunkStrategy = ChunkStrategy.HYBRID
    chunk_size: int = Field(default=512, ge=32)
    chunk_overlap: int = Field(default=64, ge=0)
    chunk_tokenizer: TokenizerName = "approximate"
    json_options: JsonProjection = Field(default_factory=JsonProjection)

    @field_validator("formats")
    @classmethod
    def unique_formats(cls, value):
        return list(dict.fromkeys(value))

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trueparse._version import get_version
from trueparse.models.enums import ChunkStrategy, ImageInclusionMode, OCRMode, OutputFormat, ParsingProfile


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)


class TableOutputOptions(Options):
    grid: bool = True
    markdown: bool = True
    html: bool = False


class ResourceLimits(Options):
    max_render_pixels: int = Field(default=25_000_000, gt=0)
    max_total_render_pixels: int = Field(default=100_000_000, gt=0)
    max_ocr_pages: int = Field(default=200, gt=0)
    max_asset_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_response_bytes: int = Field(default=128 * 1024 * 1024, gt=0)
    max_nodes: int = Field(default=200_000, gt=0)
    max_table_cells: int = Field(default=250_000, gt=0)


class JsonOptions(Options):
    coordinates: bool = True
    text_only: bool = False
    metadata: bool = True
    hierarchy: bool = True
    quality_scores: bool = False
    relationships: bool = True
    images: ImageInclusionMode = ImageInclusionMode.PATH
    tables: TableOutputOptions = Field(default_factory=TableOutputOptions)

    @field_validator("tables", mode="before")
    @classmethod
    def table_boolean(cls, value):
        if isinstance(value, bool):
            return {} if value else {"grid": False, "markdown": False, "html": False}
        return value


class OutputConfig(Options):
    formats: list[OutputFormat] = Field(
        default_factory=lambda: [OutputFormat.JSON, OutputFormat.MARKDOWN], min_length=1
    )
    json_options: JsonOptions = Field(default_factory=JsonOptions)
    save_to_disk: bool = True


class ParseOptions(Options):
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    strict: bool = False
    profile: ParsingProfile = ParsingProfile.BALANCED
    debug: bool = False
    output_config: OutputConfig = Field(default_factory=OutputConfig)
    extract_images: bool = True
    extract_tables: bool = True
    extract_charts: bool = True
    extract_formulas: bool = True
    ocr: OCRMode = OCRMode.AUTO
    password: str | None = None
    output_path: str | None = "data/output"
    render_dpi: int | None = Field(default=None, ge=36, le=600)
    max_pages: int | None = Field(default=None, gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_file_size_mb: int = Field(default=200, gt=0)
    chunk_strategy: ChunkStrategy = ChunkStrategy.HYBRID
    chunk_max_tokens: int = Field(default=512, ge=32)
    chunk_overlap_tokens: int = Field(default=64, ge=0)
    chunk_tokenizer: str = "approximate"


class EngineConfig(Options):
    default_output_root: str = "data/output"
    engine_version: str = Field(default_factory=get_version)

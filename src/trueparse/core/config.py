
from pydantic import BaseModel, Field

from trueparse.core.enums import ChunkStrategy, OCRMode, ParsingProfile
from trueparse.core.version import get_version


class ParseOptions(BaseModel):
    profile: ParsingProfile = Field(
        default=ParsingProfile.BALANCED,
        description=(
            "Parsing profile. Selects render DPI, table strategies, OCR "
            "aggressiveness and layout post-processing "
            "(fast, balanced, accurate, maximum_accuracy)"
        )
    )
    debug: bool = Field(
        default=False,
        description="Enable debug output (page renders, intermediate structures)"
    )
    extract_images: bool = Field(
        default=True,
        description="Extract embedded visual raster images"
    )
    extract_tables: bool = Field(
        default=True,
        description="Detect and structurally reconstruct tables"
    )
    extract_charts: bool = Field(
        default=True,
        description="Detect and extract charts/diagrams"
    )
    extract_formulas: bool = Field(
        default=True,
        description="Detect mathematical equations/formulas and tag them as EQUATION elements"
    )
    ocr: OCRMode = Field(
        default=OCRMode.AUTO,
        description=(
            "OCR mode: auto (only pages detected as scanned), always, never. "
            "Requires the optional 'ocr' extra: pip install trueparse[ocr]"
        )
    )
    password: str | None = Field(
        default=None,
        description="Password used to unlock an encrypted PDF"
    )
    output_path: str | None = Field(
        default="data/output",
        description="Root output directory for results and assets"
    )
    render_dpi: int | None = Field(
        default=None,
        description=(
            "DPI for region clipping and debug rendering. "
            "Defaults to the value implied by the selected profile."
        )
    )
    max_pages: int | None = Field(
        default=None,
        description="Limit maximum number of pages to parse (for fast sampling)"
    )
    max_file_size_mb: int = Field(
        default=200,
        description="Maximum PDF file size in MB"
    )

    # --- Retrieval chunking ------------------------------------------------
    emit_chunks: bool = Field(
        default=False,
        description="Write chunks.jsonl alongside document.json for RAG ingestion"
    )
    chunk_strategy: ChunkStrategy = Field(
        default=ChunkStrategy.HYBRID,
        description="Chunking strategy: section, token, or hybrid"
    )
    chunk_max_tokens: int = Field(
        default=512,
        ge=32,
        description="Approximate token ceiling per chunk"
    )
    chunk_overlap_tokens: int = Field(
        default=64,
        ge=0,
        description="Approximate tokens of trailing context repeated into the next chunk"
    )

    # --- Additional export formats ----------------------------------------
    emit_html: bool = Field(
        default=False,
        description="Write a standalone document.html rendering alongside the JSON"
    )
    emit_text: bool = Field(
        default=False,
        description="Write a plain document.txt rendering alongside the JSON"
    )


class EngineConfig(BaseModel):
    default_output_root: str = "data/output"
    schema_version: str = "1.1"
    engine_version: str = Field(default_factory=get_version)
    asset_dir_name: str = "assets"
    output_dir_name: str = "output"
    max_batch_size: int = 100
    max_workers: int = 4
    debug_dir_name: str = "debug"
    source_dir_name: str = "source"

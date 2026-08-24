from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

from trueparse.core.enums import ParsingProfile, OCRMode


class ParseOptions(BaseModel):
    profile: ParsingProfile = Field(
        default=ParsingProfile.BALANCED,
        description="Parsing profile (fast, balanced, accurate, maximum_accuracy)"
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
        description="Detect and extract mathematical equations/formulas"
    )
    ocr: OCRMode = Field(
        default=OCRMode.AUTO,
        description="OCR mode: auto (only if needed), always, never"
    )
    output_path: Optional[str] = Field(
        default="data/output",
        description="Root output directory for results and assets"
    )
    render_dpi: int = Field(
        default=150,
        description="DPI for region clipping and debug rendering"
    )
    max_pages: Optional[int] = Field(
        default=None,
        description="Limit maximum number of pages to parse (for fast sampling)"
    )
    max_file_size_mb: int = Field(
        default=200,
        description="Maximum PDF file size in MB"
    )


class EngineConfig(BaseModel):
    default_output_root: str = "data/output"
    schema_version: str = "1.0"
    engine_version: str = "0.1.0"
    asset_dir_name: str = "assets"
    output_dir_name: str = "output"
    max_batch_size: int = 100
    max_workers: int = 4
    debug_dir_name: str = "debug"
    source_dir_name: str = "source"

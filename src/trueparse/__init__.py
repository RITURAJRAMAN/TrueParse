"""TrueParse - Open-source canonical PDF parsing and document understanding engine."""

from trueparse.pipeline.runner import PDFParser
from trueparse.core.config import ParseOptions, EngineConfig
from trueparse.core.enums import ParsingProfile, OCRMode, ElementType, AssetType, SourceMethod, RelationshipType
from trueparse.core.models import (
    Document,
    Page,
    GenericElement,
    DocumentElement,
    FigureElement,
    Asset,
    AssetOccurrence,
    Section,
    Relationship,
    SourceProvenance,
    BoundingBox,
    DocumentMetadata,
    DocumentQuality,
    PageQuality,
)

__version__ = "0.1.0"

__all__ = [
    "PDFParser",
    "ParseOptions",
    "EngineConfig",
    "ParsingProfile",
    "OCRMode",
    "ElementType",
    "AssetType",
    "SourceMethod",
    "RelationshipType",
    "Document",
    "Page",
    "GenericElement",
    "DocumentElement",
    "FigureElement",
    "Asset",
    "AssetOccurrence",
    "Section",
    "Relationship",
    "SourceProvenance",
    "BoundingBox",
    "DocumentMetadata",
    "DocumentQuality",
    "PageQuality",
    "__version__",
]

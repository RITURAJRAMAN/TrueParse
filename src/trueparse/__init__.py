"""TrueParse - Open-source canonical PDF parsing and document understanding engine."""

from trueparse.chunking.chunker import DocumentChunker
from trueparse.core.config import EngineConfig, ParseOptions
from trueparse.core.enums import (
    AssetType,
    ChunkStrategy,
    ElementType,
    ErrorCode,
    OCRMode,
    ParsingProfile,
    RelationshipType,
    SourceMethod,
)
from trueparse.core.errors import PDFEngineError
from trueparse.core.models import (
    Asset,
    AssetOccurrence,
    BoundingBox,
    Chunk,
    Document,
    DocumentElement,
    DocumentMetadata,
    DocumentQuality,
    FigureElement,
    GenericElement,
    Page,
    PageQuality,
    Relationship,
    Section,
    SourceProvenance,
    TableCell,
    TableElement,
)
from trueparse.core.profiles import ProfileSettings
from trueparse.core.version import get_version
from trueparse.ocr.engine import ocr_available
from trueparse.pdf.inspector import DocumentInspection, PDFInspector
from trueparse.pipeline.runner import PDFParser
from trueparse.serializer.html import HTMLExporter, TextExporter
from trueparse.serializer.json import JSONSerializer
from trueparse.serializer.markdown import MarkdownExporter

__version__ = get_version()

__all__ = [
    # Engine
    "PDFParser",
    "PDFInspector",
    "DocumentInspection",
    "DocumentChunker",
    # Configuration
    "ParseOptions",
    "EngineConfig",
    "ProfileSettings",
    # Enums
    "ParsingProfile",
    "OCRMode",
    "ChunkStrategy",
    "ElementType",
    "AssetType",
    "SourceMethod",
    "RelationshipType",
    "ErrorCode",
    # Models
    "Document",
    "Page",
    "Chunk",
    "GenericElement",
    "DocumentElement",
    "FigureElement",
    "TableElement",
    "TableCell",
    "Asset",
    "AssetOccurrence",
    "Section",
    "Relationship",
    "SourceProvenance",
    "BoundingBox",
    "DocumentMetadata",
    "DocumentQuality",
    "PageQuality",
    # Serializers
    "JSONSerializer",
    "MarkdownExporter",
    "HTMLExporter",
    "TextExporter",
    # Errors & capabilities
    "PDFEngineError",
    "ocr_available",
    "get_version",
    "__version__",
]

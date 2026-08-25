from enum import Enum


class ElementType(str, Enum):
    TITLE = "title"
    SECTION_HEADER = "section_header"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    IMAGE = "image"
    CHART = "chart"
    DIAGRAM = "diagram"
    EQUATION = "equation"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    FOOTNOTE = "footnote"
    PAGE_NUMBER = "page_number"
    FORM = "form"
    CODE = "code"
    UNKNOWN = "unknown"


class AssetType(str, Enum):
    IMAGE = "image"
    FIGURE = "figure"
    CHART = "chart"
    DIAGRAM = "diagram"
    FORMULA = "formula"


class SourceMethod(str, Enum):
    NATIVE_PDF = "native_pdf"
    EMBEDDED_PDF_IMAGE = "embedded_pdf_image"
    VECTOR_CROP = "vector_crop"
    GEOMETRIC_ANALYSIS = "geometric_analysis"
    OCR_MODEL = "ocr_model"
    TABLE_STRUCTURE_MODEL = "table_structure_model"
    CHART_PARSER = "chart_parser"
    FORMULA_OCR = "formula_ocr"
    VLM = "vlm"
    HEURISTIC = "heuristic"


class ParsingProfile(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    ACCURATE = "accurate"
    MAXIMUM_ACCURACY = "maximum_accuracy"


class OCRMode(str, Enum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class RelationshipType(str, Enum):
    CONTAINS = "contains"
    HAS_CAPTION = "has_caption"
    REFERENCES = "references"
    CONTINUES = "continues"
    SECTION_CHILD = "section_child"


class ChunkStrategy(str, Enum):
    """How the RAG chunker splits a parsed document."""
    SECTION = "section"      # one chunk per leaf section, never split
    TOKEN = "token"          # fixed token budget with overlap, ignores sections
    HYBRID = "hybrid"        # split on sections first, then token-budget within


class ErrorCode(str, Enum):
    INVALID_PDF = "INVALID_PDF"
    PDF_PARSE_ERROR = "PDF_PARSE_ERROR"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    PDF_PASSWORD_REQUIRED = "PDF_PASSWORD_REQUIRED"
    PDF_PASSWORD_INCORRECT = "PDF_PASSWORD_INCORRECT"
    PDF_RESOURCE_LIMIT = "PDF_RESOURCE_LIMIT"
    PAGE_RENDER_ERROR = "PAGE_RENDER_ERROR"
    OCR_ERROR = "OCR_ERROR"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    LAYOUT_ERROR = "LAYOUT_ERROR"
    TABLE_EXTRACTION_ERROR = "TABLE_EXTRACTION_ERROR"
    CHART_EXTRACTION_ERROR = "CHART_EXTRACTION_ERROR"
    ASSET_STORAGE_ERROR = "ASSET_STORAGE_ERROR"
    SERIALIZATION_ERROR = "SERIALIZATION_ERROR"
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"

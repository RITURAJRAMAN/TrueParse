"""TrueParse: local PDF extraction with explicit outputs and persistence."""

from trueparse._version import get_version
from trueparse.config.options import ResourceLimits
from trueparse.contracts import CanonicalDocument
from trueparse.contracts.request import JsonProjection, ParseRequest
from trueparse.contracts.schema import get_schema
from trueparse.exceptions import PDFEngineError as ParseError
from trueparse.sdk.client import TrueParseClient
from trueparse.sdk.local import aparse, parse
from trueparse.sdk.result import ParseResult

__version__ = get_version()

__all__ = [
    "parse",
    "aparse",
    "ParseRequest",
    "ParseResult",
    "CanonicalDocument",
    "get_schema",
    "ResourceLimits",
    "JsonProjection",
    "TrueParseClient",
    "ParseError",
    "__version__",
]

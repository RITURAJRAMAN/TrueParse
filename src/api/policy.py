from __future__ import annotations

import json
import os

from pydantic import ValidationError

from trueparse.config.options import ResourceLimits
from trueparse.contracts.request import ParseRequest
from trueparse.exceptions import PDFEngineError
from trueparse.models.enums import ErrorCode, ImageInclusionMode


def parse_options(encoded: str) -> ParseRequest:
    try:
        options = ParseRequest.model_validate_json(encoded)
    except ValidationError as exc:
        raise PDFEngineError(
            ErrorCode.INVALID_OPTIONS, json.dumps(exc.errors(include_input=False, include_url=False))
        ) from exc
    if options.images == ImageInclusionMode.PATH:
        raise PDFEngineError(ErrorCode.INVALID_OPTIONS, "HTTP supports images='none' or 'base64'.")
    cap = max(1.0, float(os.environ.get("TRUEPARSE_TIMEOUT_SECONDS", "120")))
    if options.timeout_seconds is not None and options.timeout_seconds > cap:
        raise PDFEngineError(
            ErrorCode.INVALID_OPTIONS, f"Requested timeout exceeds the server limit of {cap:g} seconds."
        )
    options.timeout_seconds = options.timeout_seconds or cap
    return options


def apply_limits(options: ParseRequest, ceilings: ResourceLimits) -> ParseRequest:
    caps = ceilings.model_dump()
    requested = options.limits.model_dump()
    if any(
        value > caps[name] for name, value in requested.items() if name in options.limits.model_fields_set
    ):
        raise PDFEngineError(ErrorCode.INVALID_OPTIONS, "Requested limits exceed server ceilings.")
    return options.model_copy(
        update={
            "limits": ResourceLimits(**{name: min(value, caps[name]) for name, value in requested.items()})
        }
    )

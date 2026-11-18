from __future__ import annotations

import json
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel

from trueparse.contracts.document import CanonicalDocument

SchemaName = Literal["document", "request", "response", "chunk", "job", "batch", "outcome"]


def schema_version(name: SchemaName) -> str:
    return "1.0" if name in ("job", "batch", "outcome") else "3.0"


def build_schema(name: SchemaName) -> dict:
    from trueparse.contracts.chunks import Chunk
    from trueparse.contracts.jobs import BatchStatus, JobStatus, ParseOutcome
    from trueparse.contracts.request import ParseRequest
    from trueparse.sdk.result import ParseResult

    models: dict[str, type[BaseModel]] = {
        "document": CanonicalDocument,
        "request": ParseRequest,
        "response": ParseResult,
        "chunk": Chunk,
        "job": JobStatus,
        "batch": BatchStatus,
        "outcome": ParseOutcome,
    }
    model = models[name]
    schema = model.model_json_schema(mode="serialization" if name == "response" else "validation")
    if name in ("response", "outcome"):
        document = CanonicalDocument.model_json_schema()
        schema.setdefault("$defs", {}).update(document.pop("$defs", {}))
        schema["$defs"]["CanonicalDocument"] = document
        schema["$defs"]["ParsedOutputData"]["properties"]["json"] = {
            "anyOf": [{"$ref": "#/$defs/CanonicalDocument"}, {"type": "null"}],
            "default": None,
        }
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:trueparse:schema:{schema_version(name)}:{name}"
    return schema


def get_schema(name: SchemaName = "document") -> dict:
    if name not in ("document", "request", "response", "chunk", "job", "batch", "outcome"):
        raise ValueError(f"Unknown schema: {name}")
    return json.loads(
        files("trueparse.contracts")
        .joinpath("schemas")
        .joinpath(f"{name}-{schema_version(name)}.json")
        .read_text(encoding="utf-8")
    )

from __future__ import annotations

from trueparse.core.models import Document


class JSONSerializer:
    """Serializes canonical Document model to deterministic, validated JSON."""

    @staticmethod
    def serialize(document: Document, indent: int = 2) -> str:
        # pydantic model_dump_json handles serialization with schema validation
        return document.model_dump_json(indent=indent)

    @staticmethod
    def deserialize(json_str: str) -> Document:
        return Document.model_validate_json(json_str)

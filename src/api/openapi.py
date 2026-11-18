from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from trueparse.contracts.request import ParseRequest


class Application(FastAPI):
    def openapi(self) -> dict[str, Any]:
        schema = super().openapi()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        model = ParseRequest.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(model.pop("$defs", {}))
        components["ParseRequest"] = model
        from trueparse.contracts.document import CanonicalDocument

        document = CanonicalDocument.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(document.pop("$defs", {}))
        components["CanonicalDocument"] = document
        components["ParsedOutputData"]["properties"]["json"] = {
            "anyOf": [{"$ref": "#/components/schemas/CanonicalDocument"}, {"type": "null"}]
        }
        return schema

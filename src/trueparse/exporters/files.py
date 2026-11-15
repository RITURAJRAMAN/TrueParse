from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

from trueparse.contracts.response import ParsedOutputData
from trueparse.models.enums import OutputFormat

_FIELDS = {
    OutputFormat.JSON: ("document", "json"),
    OutputFormat.MARKDOWN: ("markdown", "md"),
    OutputFormat.HTML: ("html", "html"),
    OutputFormat.TEXT: ("text", "txt"),
    OutputFormat.CHUNKS: ("chunks", "chunks.jsonl"),
}


def serialize_outputs(output: ParsedOutputData, formats: Iterable[OutputFormat]) -> Iterator[tuple[str, str]]:
    for fmt in dict.fromkeys(formats):
        field, extension = _FIELDS[fmt]
        value = getattr(output, field)
        if value is None:
            continue
        if fmt == OutputFormat.JSON:
            value = json.dumps(value, indent=2, ensure_ascii=False)
        elif fmt == OutputFormat.CHUNKS:
            value = "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in value)
        yield extension, value

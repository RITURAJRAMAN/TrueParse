# TrueParse

Local PDF extraction for applications and retrieval pipelines.

TrueParse turns a PDF into readable Markdown, structured document JSON, plain
text, HTML, or source-backed chunks. You choose the outputs. Nothing is saved
until your application explicitly saves the result.

This checkout is the **0.2.0 developer preview**. It has not been published to PyPI.

## Start with one call

Install from this checkout:

```bash
pip install .
```

```python
from trueparse import parse

result = parse("report.pdf")
print(result.markdown)
```

The default is Markdown, local automatic OCR when available, no images, and no
output files. Install `pip install ".[ocr]"` to recognize scanned documents.
Native PDFs work without the OCR extra.

## Request what your application needs

```python
result = parse(
    "report.pdf",
    formats=["markdown", "json", "chunks"],
    images="base64",
    json_options={"coordinates": True},
)

print(result.markdown)
for node in result.iter_nodes():
    print(node.type, node.id, node.content)
print(result.chunks)
print(result.warnings)
```

Supported formats are `markdown`, `json`, `text`, `html`, and `chunks`.
Unrequested outputs are `None`. Results are typed Pydantic models;
`result.model_dump(mode="json")` produces the HTTP response representation.

`images` controls visual delivery across all exports:

- `none` (default): omit visual assets.
- Visual occurrences remain in JSON when bytes are omitted; check their
  `availability` instead of treating missing bytes as missing document content.
- `base64`: self-contained embedded images in JSON, Markdown, and HTML.
- `path` (local SDK): relative links plus `result.get_asset(asset_id)`; save the
  result to materialize the linked files.

`json_options` affects **only JSON**: coordinates, document metadata, hierarchy,
quality metrics, relationships, text-only projection, and table representations.
It does not change Markdown, HTML, or chunks. JSON coordinates default to false;
chunks retain available source provenance independently.

Document JSON uses schema **3.0**: `nodes` stores each logical element once,
`reading_order` supplies its traversal order, and `pages[].node_ids` links physical
placements. A cross-page table has multiple source fragments and one logical
node. Images are separate assets referenced by visual occurrences.

```python
from trueparse import CanonicalDocument, get_schema

document = CanonicalDocument.model_validate(result.document)
schema = get_schema("document")
```

Results expose `status`, `scope`, structured `issues`, and per-format
`representations` accounting. `partial` signals known extraction failures.
Image omission is an output choice, not an extraction error. Success does not
certify perfect accuracy. Headers/footers are preserved; metadata titles are not
inserted into body content. Plain-text tables use tab-separated cells; Markdown
uses embedded HTML when cell spans require it.

## Save explicitly

```python
result.save("exports/report")
```

Use a new or empty directory. TrueParse refuses a nonempty directory, protecting
existing files and preventing outputs from different runs from getting mixed.
For `report.pdf`, the directory contains only requested `report.md`, `report.json`,
`report.chunks.jsonl`, etc., plus populated `assets/images/` or `assets/figures/`
when path-based images were requested. The source PDF is never copied.

## Use the same result over HTTP

Install and start the stateless service:

```bash
pip install ".[api]"
trueparse serve
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
The service has one parsing endpoint, `POST /v1/parse`, plus `GET /health`.

```bash
curl http://127.0.0.1:8000/v1/parse \
  -F 'file=@report.pdf' \
  -F 'options={"formats":["markdown","json"],"images":"base64"}'
```

A Python HTTP client uses the same options and result properties:

```bash
pip install ".[client]"
```

```python
from trueparse import TrueParseClient

with TrueParseClient("http://127.0.0.1:8000") as client:
    result = client.parse("report.pdf", formats=["markdown", "json"])
    print(result.markdown)
    result.save("exports/remote-report")
```

The service keeps bounded uploads in memory, applies a hard execution deadline,
and returns the requested data. It creates no document artifact directories,
upload staging files, or job database. Save or store the response in your own
application. HTTP image delivery is `none` or `base64`.

## Async applications and existing queues

```python
from trueparse import aparse

async def ingest(path):
    result = await aparse(path, formats=["markdown", "chunks"])
    return result.model_dump(mode="json")
```

Use `parse()` inside your existing Celery, RQ, task runner, or worker. TrueParse
owns extraction; your application owns scheduling, retries, storage, and indexing.
There is no second queue or database to operate.

## CLI

```bash
trueparse parse report.pdf                         # Markdown to stdout
trueparse parse report.pdf -f json -f chunks       # Response JSON to stdout
trueparse parse report.pdf -o exports/report       # Explicit save
trueparse parse report.pdf --images base64 -f json
trueparse inspect report.pdf
```

The CLI uses the same options and extraction contract as the SDK.

## Errors, limits, and advanced controls

```python
from trueparse import ParseError, parse

try:
    result = parse("private.pdf", password="example", max_pages=10)
except ParseError as error:
    print(error.code.value, error.message)
```

Page failures produce warnings while recoverable pages continue. Warnings remain
visible even when JSON and quality scores were not requested. Unknown request
options are rejected rather than silently ignored.

Common controls are `profile="fast"|"balanced"|"accurate"|"maximum_accuracy"`,
`ocr="auto"|"always"|"never"`, `max_pages`, `chunk_size`, and `chunk_overlap`.
Chunk budgets use an approximate word counter by default. For exact
`cl100k_base` or `o200k_base` counts, provision the optional tokenizer during setup:

```bash
pip install ".[tokenizer]"
python -c "import tiktoken; [tiktoken.get_encoding(n) for n in ('cl100k_base', 'o200k_base')]"
```

Then pass `chunk_tokenizer="o200k_base"` with `formats=["chunks"]` in either
the SDK or HTTP options. Parsing never downloads encoding data. For offline
deployment, use the same persistent `TIKTOKEN_CACHE_DIR` during setup and parsing,
or set `TRUEPARSE_TOKENIZER_DIR` to a directory containing named `.tiktoken` files.
`TRUEPARSE_TOKENIZER_FILE` supports a single encoding file.
Chunks include document-scoped IDs, `source_spans`, the counter name, and
`budget_exceeded` when an indivisible table row exceeds the requested budget.
Source text offsets are zero-based with exclusive ends; coordinates use
unrotated PDF points, measured from the top left.

`result.metrics["stage_timings_ms"]` and `result.metrics["stage_calls"]` expose
the work actually performed. Text-only requests skip unused table renderers and
document graphs.

For a hard local deadline, set `timeout_seconds`; parsing then runs in a supervised
process. Standalone scripts using this option must put the call under the standard
`if __name__ == "__main__":` guard. Ordinary local calls need no child process.
The HTTP service always enforces its configured deadline.

Set `strict=True` to reject known extraction issues with `EXTRACTION_INCOMPLETE`.
This cannot detect unknown omissions. Resource exhaustion raises
`PDF_RESOURCE_LIMIT`; results are never silently truncated. Pass `limits` as a
dictionary or public `ResourceLimits` model. Defaults are 25 million pixels per
render, 100 million rendered pixels total, 200 OCR pages, 64 MiB of assets,
128 MiB of serialized response, 200,000 nodes and 250,000 table cells.

Normalized paragraphs retain `source_texts` and `normalization_map` when changed.
Lists expose nested `children`. Visuals inside tables remain separate occurrences
linked by `parent_table_id`. Cells containing an uninterpreted vector annotation
use `kind="visual_marker"`; the marker does not claim a recognized value.

PDF extraction is heuristic. Complex tables, unusual fonts, and reading order
need validation on your own documents. Quality scores are diagnostics, not
calibrated probabilities. TrueParse does not promise chart-series understanding,
general handwritten-document recognition, LaTeX reconstruction, or perfect PDF
layout reproduction.

## Development and deployment

```bash
uv sync --frozen --extra dev --extra ocr --extra tokenizer
uv run pytest
uv run ruff check src tests tools
uv run mypy
uv run python -B tools/update_schemas.py
```

See the [API References](docs/API_References.md),
[SDK References](docs/SDK_References.md) for usage and integration examples.

### Source organization

```text
src/trueparse/
  api/          HTTP app, routes, uploads, settings and error responses
  cli/          Command-line entry points
  sdk/          Public local facade, HTTP client and result helpers
  contracts/    Public request/response/document models and JSON schemas
  config/       Parser configuration and extraction profiles
  models/       Internal document, element, asset and geometry models
  extraction/   PDF text/images/drawings, tables and OCR adapters
  processing/   Classification, typography, reading order and reconstruction
  pipeline/     Orchestration, per-page stages and result assembly
  exporters/    JSON projection, Markdown, HTML, text and table rendering
  chunking/     Retrieval chunks and token counting
  io/           Source loading, path containment and filesystem persistence
  runtime/      Worker supervision, resource budgets and timing
```

Import the supported SDK from `trueparse`. Extraction adapters recover evidence;
processing modules reconstruct logical content; exporters render the resulting
models. HTTP and CLI adapters use the shared request contract. The ASGI entry
point is `trueparse.api.app:app`.

Independent accuracy evaluation and platform/container execution remain release
requirements.

The reference documents include Python and JavaScript integration examples.

Release history is in the [changelog](CHANGELOG.md).

Docker is optional: `docker compose up --build` starts the same stateless service.
The image can omit OCR with `--build-arg INSTALL_OCR=false`.

MIT license for TrueParse source; dependencies retain their own licenses.

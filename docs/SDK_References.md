# TrueParse SDK References

Python reference for the 0.2.0 developer preview and document schema 3.0.
This document covers the local SDK, result helpers and Python HTTP client.
For endpoints, HTTP status codes and server deployment, see [API References](API_References.md).

## Contents

- [Installation](#installation)
- [Choose the right interface](#choose-the-right-interface)
- [Quick start](#quick-start)
- [parse](#parse)
- [aparse](#aparse)
- [ParseRequest and JsonProjection](#parserequest-and-jsonprojection)
- [Profiles and OCR](#profiles-and-ocr)
- [Formats, images and projections](#formats-images-and-projections)
- [ParseResult](#parseresult)
- [Document JSON and CanonicalDocument](#document-json-and-canonicaldocument)
- [Chunks and tokenization](#chunks-and-tokenization)
- [ResourceLimits and deadlines](#resourcelimits-and-deadlines)
- [ParseError](#parseerror)
- [TrueParseClient](#trueparseclient)
- [Application recipes](#application-recipes)
- [Schemas and integration boundaries](#schemas-and-integration-boundaries)

## Installation

Install from the repository root; the preview has not been published to PyPI:

```bash
pip install .
```

| Extra | Install | Use |
| --- | --- | --- |
| Core | `pip install .` | Local native-PDF conversion |
| OCR | `pip install ".[ocr]"` | Local recognition of image/scanned text |
| Tokenizer | `pip install ".[tokenizer]"` | Exact cl100k_base/o200k_base chunk counting |
| Client | `pip install ".[client]"` | Python HTTP facade |

Combine extras, for example `pip install ".[ocr,tokenizer,client]"`. Python >=3.10
is declared by the package; the latest local verification used Python 3.12.
Platform/version matrix certification is separate from this reference.

Supported imports:

```python
from trueparse import (
    CanonicalDocument,
    JsonProjection,
    ParseError,
    ParseRequest,
    ParseResult,
    ResourceLimits,
    TrueParseClient,
    __version__,
    aparse,
    get_schema,
    parse,
)
```

Use these public entry points instead of constructing the internal pipeline.
There is no compatibility promise for retired internal queue/preset contracts.
The SDK implementation is organized under `trueparse.sdk`; public root imports
above remain the supported integration surface. Internal extraction, processing,
export, I/O and runtime modules are separate implementation details.

## Choose the right interface

| Situation | Interface/options |
| --- | --- |
| Synchronous local script or existing worker | `parse()` |
| Async web application/event loop | `await aparse()` |
| Service runs elsewhere | `TrueParseClient.parse()` or `.aparse()` |
| LLM accepts readable text | `formats=["markdown"]` or `["text"]` |
| Application needs tables/structured content | `formats=["json"]` |
| Multimodal model input | `formats=["json"], images="base64"` |
| Retrieval indexing | `formats=["chunks"]`, optionally with JSON |
| Local export with image files | `images="path"`, followed by `result.save()` |
| Persistent result envelope | `result.model_dump(mode="json")` / `.model_dump_json()` |

TrueParse performs conversion. Your application chooses storage, retries, job
scheduling, embeddings, search indexes and model-specific message formats.

## Quick start

```python
from trueparse import parse

result = parse("report.pdf", formats=["markdown", "json"])
print(result.markdown)
print(result.status)
for node in result.iter_nodes():
    print(node.type, node.id, node.content)
```

Default behavior is Markdown, no images, balanced profile, automatic OCR where
available, no deadline and no output files. Results remain in memory until you
explicitly save them. Even a completed result is not proof of perfect extraction.

## parse

```text
parse(
    source,
    *,
    formats=("markdown",),
    images="none",
    profile="balanced",
    ocr="auto",
    filename=None,
    password=None,
    max_pages=None,
    timeout_seconds=None,
    chunk_strategy="hybrid",
    chunk_size=512,
    chunk_overlap=64,
    chunk_tokenizer="approximate",
    json_options=None,
    strict=False,
    limits=None,
) -> ParseResult
```

### Sources

| Source | Example | Behavior |
| --- | --- | --- |
| String path | `parse("report.pdf")` | Reads a local PDF |
| `pathlib.Path` | `parse(Path("report.pdf"))` | Same as a string path |
| `bytes` | `parse(pdf_bytes, filename="report.pdf")` | Parses bytes without saving the PDF |
| Binary file object | `parse(stream, filename="report.pdf")` | Reads from the current stream position |

For seekable streams, the original position is restored after reading. A
non-seekable stream is consumed. Rewind a stream yourself when its current
position is not the PDF start. Input must produce bytes; text-mode files are
invalid. A URL string is treated as a local path, not fetched. The SDK input
ceiling is 200 MiB; it is not configured by `ResourceLimits`.

`filename` overrides the output/source filename; bytes and streams otherwise
default to `document.pdf`. Directory components are stripped. It is metadata
and an export stem, not an output directory or a replacement for source bytes.

```python
from pathlib import Path
from trueparse import parse

pdf_bytes = Path("report.pdf").read_bytes()
result = parse(pdf_bytes, filename="quarterly-report.pdf", formats=["text"])

with open("report.pdf", "rb") as stream:
    other = parse(stream, filename="report.pdf", formats=["json"])
```

### Parameters

Strings are accepted for enum-valued options; internal enum imports are unnecessary.

| Parameter | Type/values | Default | How and when to use |
| --- | --- | --- | --- |
| `formats` | Sequence of `markdown`, `json`, `html`, `text`, `chunks` | `("markdown",)` | Request one or several outputs; must not be empty |
| `images` | `none`, `base64`, `path` | `none` | Select visual delivery across outputs |
| `profile` | `fast`, `balanced`, `accurate`, `maximum_accuracy` | `balanced` | Extraction effort and heuristics |
| `ocr` | `auto`, `always`, `never` | `auto` | Text recognition policy |
| `filename` | String or None | Source basename / `document.pdf` | Naming for byte/stream inputs and explicit exports |
| `password` | String or None | None | Unlock encrypted input |
| `max_pages` | Positive integer or None | None | Process first N pages; no arbitrary page-range selector |
| `timeout_seconds` | Positive number or None | None | Hard deadline using a supervised child process |
| `chunk_strategy` | `hybrid`, `token`, `section` | `hybrid` | Chunk boundaries |
| `chunk_size` | Integer >=32 | 512 | Token/hybrid chunk target ceiling |
| `chunk_overlap` | Integer >=0 | 64 | Repeated text context between chunks |
| `chunk_tokenizer` | `approximate`, `cl100k_base`, `o200k_base` | `approximate` | Chunk counter |
| `json_options` | `JsonProjection`, dict or None | Default projection | JSON-only field selection |
| `strict` | Boolean | false | Reject known incomplete extraction |
| `limits` | `ResourceLimits`, dict or None | Default limits | Resource ceilings |

Pass formats as a list/tuple, not the string `"json"`. Repeated format names are
deduplicated. Chunk settings are used only when chunks are requested. JSON-only
choices do not modify other exports. Unknown keyword arguments to `parse()` raise
Python `TypeError`; invalid recognized options raise `ParseError(INVALID_OPTIONS)`.

## aparse

```python
async def aparse(source, **options) -> ParseResult: ...
```

`aparse()` accepts the same options as `parse()` and runs it in a background
thread, keeping the event loop responsive.

```python
from trueparse import aparse

async def ingest(pdf_bytes):
    result = await aparse(
        pdf_bytes,
        filename="upload.pdf",
        formats=["json", "chunks"],
    )
    return result.model_dump(mode="json")
```

Local PyMuPDF work is serialized by a process-local lock. Concurrent `aparse()`
calls are not a promise of parallel local extraction. Use your application worker
processes when CPU concurrency is needed. Cancelling an awaiting coroutine does
not guarantee termination of its background thread; use `timeout_seconds` for a
supervised parsing deadline. Deadline calls have the same process-startup rules
as synchronous calls.

## ParseRequest and JsonProjection

`ParseRequest` is a validated configuration model. Its fields match parse
options except `source` and `filename`, which are separate inputs. It rejects
unknown fields and nonfinite numeric values. Defaults match `parse()`.

```python
from trueparse import JsonProjection, ParseRequest, parse

request = ParseRequest(
    formats=["json", "markdown"],
    images="none",
    json_options=JsonProjection(coordinates=True),
)
result = parse("report.pdf", **request.model_dump(mode="json"))
```

`parse()` accepts keyword options, not a positional request model or a `request=`
argument. Directly constructing an invalid Pydantic model raises
`pydantic.ValidationError`; the `parse()` facade wraps its own option validation
as `ParseError`.

| `JsonProjection` field | Default | Effect |
| --- | --- | --- |
| `coordinates` | false | Include available node and cell boxes |
| `metadata` | true | Include PDF metadata |
| `hierarchy` | true | Include sections |
| `quality_scores` | false | Include heuristic diagnostics |
| `relationships` | true | Include resolvable links |
| `text_only` | false | Exclude visual nodes/assets from JSON |
| `tables.grid` | true | Structured cell records |
| `tables.markdown` | true | Table Markdown string |
| `tables.html` | false | Table HTML string |

The `tables` key accepts an object or Boolean. `tables=True` uses default table options; `tables=False` disables all three
table representation fields. Table nodes and their content still remain.
Use a dict for nested settings; no internal table-model import is required.

## Profiles and OCR

| Profile | DPI | Table strategies | Spans/continuation/paragraph merge | Typical use |
| --- | ---: | --- | --- | --- |
| `fast` | 96 | Lines | Disabled | Simple native documents and quick previews |
| `balanced` | 150 | Lines | Enabled | General default |
| `accurate` | 200 | Lines + text | Enabled | More demanding/unruled tables |
| `maximum_accuracy` | 300 | Lines + text | Enabled | Higher-effort recognition after evaluating cost |

Balanced/accurate/maximum_accuracy use automatic native-character thresholds of
30/80/200 along with scan and regional heuristics. These are implementation
heuristics, not measured accuracy percentages. Higher DPI increases pixel work.

- `ocr="never"`: native text only; useful for known native PDFs or avoiding OCR cost.
- `ocr="auto"`: recognize regions/pages when needed and a local backend is available.
- `ocr="always"`: explicitly request OCR even without the automatic page trigger.

The fast profile disables auto OCR; `ocr="always"` overrides it. OCR requires the
OCR extra and prepared local backend models. Prepare models during installation
for offline deployments. Missing/failed OCR is reported through warnings/issues
when recovery is needed; image-only text may produce partial results. OCR does
not guarantee structured recognition of scanned tables, handwriting or charts.

## Formats, images and projections

| Format | Result property | Use and limitations |
| --- | --- | --- |
| `markdown` | `.markdown` | Readable headings/lists/tables; complex spans may use embedded HTML |
| `text` | `.text` | Plain wording in reading order; tables use deterministic tab-separated cells |
| `html` | `.html` | Semantic HTML rather than pixel-positioned PDF reproduction |
| `json` | `.document` | Structured typed graph for application consumption |
| `chunks` | `.chunks` | Retrieval text and provenance; no embeddings |

Unrequested properties are None. Headers/footers are preserved; PDF metadata
titles are not inserted as invented body text. No format guarantees exact fonts,
pagination or source geometry. Text output uses visual markers where applicable,
not fabricated descriptions of pictures.

| `images` | Behavior | Choose when |
| --- | --- | --- |
| `none` | Omit bytes; retain visual occurrences in ordinary JSON | Text pipelines and lower payload size |
| `base64` | Embed data URIs in applicable exports/assets | Portable responses and multimodal input |
| `path` | Relative asset links; bytes retained in the live result | Local disk exports through `.save()` |

`images` controls all exports. `json_options` controls only document JSON.
Plain text cannot contain embedded image bytes. `text_only=True` removes visual
JSON nodes; it does not turn every picture into recognized text.

```python
from trueparse import parse

result = parse(
    "report.pdf",
    formats=["json"],
    images="none",
    json_options={
        "text_only": True,
        "tables": {"grid": True, "markdown": False, "html": False},
    },
)
```

This returns JSON without visual nodes/assets while keeping recovered text,
lists and structured tables. To retain visual occurrence metadata without
bytes, omit `text_only` and retain `images="none"`.

## ParseResult

### Properties and fields

| Member | Type | Meaning |
| --- | --- | --- |
| `document_id` | str | Source-document identity |
| `source_file` | str | Normalized original/overridden filename |
| `status` | `completed` / `partial` | Known extraction outcome |
| `formats_provided` | List of format enums | Requested distinct formats |
| `markdown`, `text`, `html` | str or None | Selected rendered outputs |
| `document` | dict or None | Selected schema 3.0 document JSON |
| `chunks` | List of dicts or None | Selected retrieval chunks |
| `data` | Output model | Underlying document/rendered output container |
| `warnings` | List of str | Human-readable diagnostics |
| `issues` | List of issue models | Structured findings |
| `scope` | Scope model | Original page count and requested/processed/failed pages |
| `representations` | Format-to-report dict | Rendered/excluded IDs and representation losses |
| `metrics` | dict | Timing/resource diagnostics |
| `page_count` | int | Convenience access to `metrics["page_count"]` |

`page_count` is the count of page records in the selected result; inspect
`scope.processed_pages` and `scope.failed_pages` for actual successful processing.
`scope.source_pages` is the original PDF page count. Page numbers are one-based.

An issue exposes `code`, `stage`, `message`, `severity`, optional `page`,
`node_ids` and `recovered`. Status is partial for failed pages or unrecovered
non-info issues. Recovered findings can remain visible in a completed result.
Choosing to omit images is not an extraction failure.

```python
if result.status == "partial":
    for issue in result.issues:
        print(issue.code, issue.page, issue.message)
for fmt, report in result.representations.items():
    print(fmt, report.losses)
```

Metrics include `duration_ms`, `page_count`, `asset_count`, `resource_usage`
(`rendered_pixels`, `ocr_pages`, `asset_bytes`), `stage_timings_ms` and
`stage_calls`. Stages can be nested; totals need not equal wall duration.
Asset count is not visual occurrence count. These are diagnostics, not measured
fidelity scores or a complete peak-memory profile.

### iter_nodes()

Returns typed nodes in canonical reading order after validating the document.
Requires `formats` to include `json`; otherwise raises `ValueError`.

```python
for node in result.iter_nodes():
    if node.type == "table":
        for cell in node.cells or []:
            print(cell.row, cell.column, cell.text)
```

### get_asset(asset_id) -> bytes

Returns bytes retained in the live local result, or decodes an available base64
asset in JSON. Raises `KeyError` when bytes are unavailable. Request visual bytes;
an omitted asset does not become downloadable just because it has an ID.

```python
result = parse("report.pdf", formats=["json"], images="base64")
for node in result.iter_nodes():
    if node.type == "visual" and node.availability == "available" and node.asset_id:
        image_bytes = result.get_asset(node.asset_id)
        print(len(image_bytes))
```

Occurrences may share an asset ID. Deduplicate by asset ID only when your task
needs unique bytes; preserve occurrences for page layout and reading order.

### save(directory) -> pathlib.Path

Saves only selected outputs into a new or empty directory. It creates parent
directories, stages the export in a sibling temporary directory, then renames
it into place. Existing nonempty destinations raise `FileExistsError`; a
symbolic-link destination raises `ValueError`. Filesystem failures can raise
ordinary `OSError` subclasses. Earlier files are not overwritten or deleted.

```python
result = parse(
    "report.pdf",
    formats=["json", "markdown", "html", "text", "chunks"],
    images="path",
)
directory = result.save("exports/report-run-001")
```

For source filename `report.pdf`, selected files are:

```text
report.json
report.md
report.html
report.txt
report.chunks.jsonl
assets/images/...     # only when populated by path-based assets
assets/figures/...    # only when populated by path-based assets
```

JSON files contain the document, not the full ParseResult envelope. Chunks use
JSON Lines, one record per line. Text files are UTF-8. The original PDF is not
copied. Base64 exports retain embedded data rather than automatically extracting
it into asset files. When using `path`, save the live result before transporting
it through JSON; private binary buffers are not part of serialization.

### Serialization and round trips

```python
from trueparse import ParseResult

payload = result.model_dump(mode="json")
encoded = result.model_dump_json()
restored = ParseResult.model_validate_json(encoded)
```

The wire envelope uses `data.json`; Python accesses that value as
`result.document` / `result.data.document`. Unrequested outputs are null.
`model_dump_json()` serializes the entire result; use it when storing warnings,
scope and metrics in addition to content. `result.save()` writes output artifacts.

Private asset buffers do not survive a JSON round trip. For portable assets,
request JSON with `images="base64"`. A deserialized path-based result cannot
materialize missing linked bytes and `.save()` raises `ValueError` in that case.

## Document JSON and CanonicalDocument

```python
from trueparse import CanonicalDocument

document = CanonicalDocument.model_validate(result.document)
for node in document.iter_nodes():
    print(node.type, node.content)
```

Validation checks typed shapes and graph invariants, not whether recovered
content exactly matches the PDF. Projected documents remain validated contracts.

| Field | Meaning |
| --- | --- |
| `schema_version` | `"3.0"`, separate from package version |
| `document_id`, `extraction_id`, `engine_version` | Source/configuration identity and engine version |
| `source` | Filename, SHA-256 and original page count |
| `scope`, `status`, `issues` | Selection and known extraction outcome |
| `metadata` | PDF metadata separately from source body |
| `pages` | One-based number, dimensions, rotation and physical node references |
| `nodes` | Typed logical nodes keyed by ID |
| `reading_order` | Logical traversal IDs, each node exactly once |
| `assets` | Deduplicated byte descriptors keyed by asset ID |
| `sections` | Section ID/title/level/parent and node references |
| `relationships` | ID/type/source/target references |
| `projection` | Excluded node IDs and omitted-field categories |
| `diagnostics` | Optional document/page quality heuristics |

Traverse `reading_order`, not dictionary key order. A continued paragraph/table
can have source fragments on several pages but remain one logical node. Physical
page traversal can repeat such a node; use logical traversal for full-document
content consumption. IDs are not promised stable across engine revisions.

### Node types

All nodes expose `id`, `type`, `role`, `content`, `source`, `method` and `engine`.
`type` selects the shape; `role` describes classification such as paragraph,
section_header, image, figure, chart, caption or unknown.

| Type | Fields specific to the type |
| --- | --- |
| `text` | Optional `level`, `original_text`, `source_texts`, `normalization_map`, `recovery_reason` |
| `list` | `ordered`, `items`; each item has `text`, optional `marker`, recursive `children` |
| `table` | `rows`, `columns`, optional `cells`, `markdown`, `html` |
| `visual` | Optional `asset_id`, optional `parent_table_id`, `availability` |

Cells expose `id`, zero-based `row`/`column`, positive `row_span`/`col_span`,
`is_header`, `text`, `kind` and `source`. `kind="visual_marker"` identifies an
uninterpreted vector annotation such as `[TrueParse vector mark]`; treat it as
an annotation requiring application review, not a recognized checkbox value.
Visuals inside tables can be separate occurrences linked by `parent_table_id`.

Each source fragment has `page`, optional `bbox`, optional `start`/`end` and
optional `source_id`. Boxes are `[x0,y0,x1,y1]` in unrotated PDF points, 1/72 inch,
from the top left. Page rotation is separate. Text offsets are Unicode code
points with exclusive ends. Normalization mappings connect normalized content
to raw `source_texts`; null source IDs represent inserted separators.

### Assets and availability

An asset has `id`, `mime_type`, optional `sha256`, pixel `width`/`height`,
`availability`, optional `path` and optional `data_uri`.

- `available`: exactly one delivery representation is present.
- `omitted`: bytes were excluded by the requested image policy.
- `unavailable`: bytes were requested but could not be supplied.

A visual occurrence may have no asset ID. Always check availability and resolve
the reference safely. Asset data is image content; it does not provide chart
series, semantic descriptions or arbitrary handwritten interpretation.

## Chunks and tokenization

```python
result = parse(
    "report.pdf",
    formats=["json", "chunks"],
    chunk_strategy="hybrid",
    chunk_size=768,
    chunk_overlap=96,
)
for chunk in result.chunks or []:
    print(chunk["id"], chunk["text"], chunk["source_spans"])
```

| Strategy | Behavior | Use |
| --- | --- | --- |
| `hybrid` | Section-aware grouping with token splitting | General RAG |
| `token` | Token grouping without section boundaries | More uniform text units |
| `section` | Section-oriented text without a text token ceiling; table row budgets still apply | Whole-section text context |

Tables are emitted separately from surrounding text and may split between rows
under every strategy. A section does not necessarily produce exactly one chunk.

Overlap is text context, not a promise of identical overlap for every table row.
If overlap >= size, the chunker reduces it to size // 4; choose a smaller explicit
overlap for predictable settings. Atomic table rows can exceed the budget and
set `budget_exceeded=True`; no source text is discarded just to fit. Very small
exact budgets can also be exceeded by an indivisible Unicode character.

Chunk records contain:

| Fields | Meaning |
| --- | --- |
| `id`, `document_id`, `chunk_index` | Identity and order |
| `text` | Retrieval representation |
| `token_estimate`, `tokenizer`, `budget_exceeded` | Counting/budget information |
| `source_spans` | Element IDs, optional original source IDs, page/box, text offsets or table row ranges and representation |
| `section_id`, `section_path` | Section context |
| `page_start`, `page_end`, `bboxes` | Page range and source regions |
| `element_ids`, `element_types`, `asset_ids` | Source content and asset references |

Text offsets point into canonical element content. `row_end` is exclusive for
table spans. Request JSON too when resolving logical element/asset IDs; JSON
projection does not mutate chunks, so a text-only projection can intentionally
exclude nodes that chunk provenance still references.

### Exact token counting

`approximate` is the default lightweight estimate. Provision exact encodings once:

```powershell
pip install ".[tokenizer]"
$env:TIKTOKEN_CACHE_DIR = 'D:/service-cache/trueparse-tokenizers'
python -c "import tiktoken; [tiktoken.get_encoding(n) for n in ('cl100k_base','o200k_base')]"
```

Then use the same cache in the parsing process:

```python
result = parse(
    "report.pdf",
    formats=["chunks"],
    chunk_tokenizer="o200k_base",
)
```

Alternatively point `TRUEPARSE_TOKENIZER_DIR` at named encoding files or use
`TRUEPARSE_TOKENIZER_FILE` for a single encoding. Missing/unreadable/mismatched
data or an absent tokenizer dependency raises `INVALID_OPTIONS`. Parsing does
not download encoding files. Choose the encoding required by your downstream
application; exact counts cover chunk text, not prompts, images, tool schemas,
message framing or model output tokens.

Runtime lookup priority is `TRUEPARSE_TOKENIZER_FILE`, `TRUEPARSE_TOKENIZER_DIR`,
`TIKTOKEN_CACHE_DIR`, `DATA_GYM_CACHE_DIR`, then the OS temporary `data-gym-cache`
directory. Prefer a persistent setup/runtime cache for production deployments.

## ResourceLimits and deadlines

```python
from trueparse import ResourceLimits, parse

result = parse(
    "report.pdf",
    formats=["json"],
    limits=ResourceLimits(max_response_bytes=32 * 1024 * 1024, max_ocr_pages=50),
)
```

A partial dict also works. Unspecified fields retain defaults; all values must
be positive integers.

| Limit | Default | Purpose |
| --- | ---: | --- |
| `max_render_pixels` | 25,000,000 | Single render/image dimension guard |
| `max_total_render_pixels` | 100,000,000 | Cumulative render work |
| `max_ocr_pages` | 200 | Unique pages requiring OCR rendering |
| `max_asset_bytes` | 67,108,864 | Accounted extracted visual bytes |
| `max_response_bytes` | 134,217,728 | Serialized result size |
| `max_nodes` | 200,000 | Content nodes before projection |
| `max_table_cells` | 250,000 | Extracted cells and declared grid sizes |

Exhaustion raises `PDF_RESOURCE_LIMIT`, without silently truncating content.
Base64 expands response size. These limits are resource guards, not a total RAM
quota. A separate table-contract validation guard rejects occupancy above one
million expanded cell positions. The 200 MiB input ceiling is independent.
Local SDK limits can be configured directly; HTTP calls remain subject to server ceilings.

### Hard timeout

Without a timeout, local parsing runs in the current process. A timeout uses a
spawned supervised process; startup contributes to the deadline. On expiry,
the worker is terminated/joined and `PARSE_TIMEOUT` is raised.

Standalone scripts using deadlines must protect their entry point:

```python
from trueparse import ParseError, parse

def main():
    try:
        result = parse("report.pdf", timeout_seconds=60)
        print(result.markdown)
    except ParseError as error:
        print(error.code.value, error.message)

if __name__ == "__main__":
    main()
```

Use an importable script/application when spawn is required; interactive notebook
or REPL startup can differ. Normal calls without a deadline do not spawn a worker.
Async cancellation by itself is not a hard execution deadline.

## ParseError

`ParseError` is the public alias for the SDK's engine exception.

```python
from trueparse import ParseError, parse

try:
    result = parse("private.pdf", password="example", strict=True)
except ParseError as error:
    print(error.code.value, error.message)
    print(error.retryable, error.details)
```

Attributes: `code` (enum; use `.value` for its string), `message`, optional
`request_id`, `document_id`, `page`, `element_id`, plus `retryable` and `details`.
`to_dict()` returns an `{"error": {...}}` object containing these fields.

| Common code | Action |
| --- | --- |
| `INVALID_OPTIONS` | Correct recognized option values, tokenizer setup or limits |
| `INVALID_PDF` | Check path/bytes and PDF structure |
| `PDF_PASSWORD_REQUIRED` | Supply a password |
| `PDF_PASSWORD_INCORRECT` | Correct the password |
| `PDF_RESOURCE_LIMIT` | Reduce workload/output or configure appropriate limits |
| `PARSE_TIMEOUT` | Review workload and deadline; marked retryable locally |
| `EXTRACTION_INCOMPLETE` | Strict acceptance failed; inspect `details["issues"]` |
| `PDF_PARSE_ERROR` | Parsing failed; inspect the exception cause/logs where available |
| `SERIALIZATION_ERROR` | Result construction or remote-response validation failed |
| `SERVICE_UNAVAILABLE` | HTTP client transport failure; marked retryable by the client |

Other defined codes include `PDF_ENCRYPTED`, `PAGE_RENDER_ERROR`, `OCR_ERROR`,
`OCR_UNAVAILABLE`, `LAYOUT_ERROR`, `TABLE_EXTRACTION_ERROR`, `CHART_EXTRACTION_ERROR`,
`ASSET_STORAGE_ERROR`, `PATH_NOT_ALLOWED`, `UPLOAD_TOO_LARGE` and
`AUTHENTICATION_FAILED`. Some stage failures are recoverable issues rather than
raised exceptions. Issue codes are stage-specific strings and are not limited
to the exception enum.

Not every failure is a `ParseError`: model construction can raise Pydantic
validation errors; unknown function keywords and nonbinary inputs can raise
`TypeError`; traversal/assets/saving can raise the documented standard errors.
Do not retry invalid options/passwords unchanged. `retryable` is a diagnostic
hint, not an automatic SDK retry policy.

### Strict acceptance

`strict=False` permits usable partial results. `strict=True` rejects failed
pages or unrecovered non-info issues before delivering exports. Deliberate page
scope and image omission are not incompleteness. Strict mode does not detect
unknown omissions or replace domain-specific validation of critical values.

## TrueParseClient

Use the Python client when your application calls a deployed TrueParse service.
The client sends bytes to that service; local `parse()` does not require one.
Install the `client` extra.

```text
TrueParseClient(
    base_url="http://127.0.0.1:8000",
    *,
    api_key=None,
    timeout=130,
)
```

| Member | Meaning |
| --- | --- |
| `base_url` | Service URL, without an endpoint suffix |
| `api_key` | Optional `X-API-Key` value |
| `timeout` | HTTPX transport timeout configuration; separate from server parsing deadline |
| `parse(source, *, filename=None, **options)` | Synchronous multipart upload returning `ParseResult` |
| `aparse(source, **options)` | Async thread wrapper around the same client call |
| `close()` | Close the underlying HTTP client |
| Context manager | `with TrueParseClient(...) as client:` closes automatically |

```python
from trueparse import TrueParseClient

with TrueParseClient("http://127.0.0.1:8000", api_key="your-key", timeout=140) as client:
    result = client.parse(
        "report.pdf",
        formats=["json", "markdown"],
        images="base64",
        timeout_seconds=90,
    )
    print(result.markdown)
```

The source types and local 200 MiB read ceiling match `parse()`. The client
validates options, sends explicitly supplied options, streams decoded response
bytes up to `limits.max_response_bytes`, and validates the resulting envelope.
It returns the same result properties and helpers as local parsing.

HTTP delivery accepts only `images="none"` or `"base64"`; requesting `path`
raises `INVALID_OPTIONS`. To retrieve remote asset bytes after deserialization,
request JSON and base64 together. `result.save()` saves returned outputs on the
client's filesystem, never on the service.

Client limits cannot raise server ceilings. Avoid dumping all defaults from a
request model into a remote call when the server uses tighter limits: explicitly
supplied limits are checked against the server ceiling. Send only intended
overrides, or use `model_dump(mode="json", exclude_unset=True)`.

Transport failures raise `SERVICE_UNAVAILABLE`; malformed successful responses
raise `SERIALIZATION_ERROR`; oversized responses raise `PDF_RESOURCE_LIMIT`.
Service errors expose their code and `details["http_status"]`. Unknown/non-JSON
remote error bodies fall back to `PDF_PARSE_ERROR`. Valid service errors preserve
retryability, request/document/element IDs, page and details. The actual HTTP
status overrides any conflicting status inside remote details. The client
performs no automatic retries.

Async example (the client is a synchronous context manager):

```python
from trueparse import TrueParseClient

async def convert_remote(pdf_bytes):
    with TrueParseClient() as client:
        result = await client.aparse(
            pdf_bytes, filename="upload.pdf", formats=["text"]
        )
        return result.text
```

HTTPX timeout and server `timeout_seconds` are different controls. Keep transport
timeouts above intended server work with headroom for upload/waiting/response.
Cancelling the coroutine does not guarantee immediate remote-worker termination.
Endpoint/authentication/server-cap details are in [API References](API_References.md).

## Application recipes

### Markdown for an LLM

```python
result = parse("report.pdf", formats=["markdown"], images="none")
if result.status == "partial":
    raise RuntimeError(f"Review extraction issues: {result.issues}")
llm_input = result.markdown
```

For models that cannot interpret Markdown's embedded HTML tables, inspect complex
table losses and choose structured cells or an application-specific table renderer.
No model provider is called by this SDK recipe.

### Structured table records

```python
result = parse(
    "report.pdf",
    formats=["json"],
    json_options={"tables": {"grid": True, "markdown": False, "html": False}},
)
tables = []
for node in result.iter_nodes():
    if node.type == "table":
        tables.append({
            "id": node.id,
            "rows": node.rows,
            "columns": node.columns,
            "cells": [cell.model_dump(mode="json") for cell in node.cells or []],
        })
```

Keep spans, headers and provenance when transforming tables. Empty cells and
visual annotations need application-specific interpretation. Do not split
Markdown strings to recover structure already present in cells.

### Ordered multimodal input

```python
result = parse("report.pdf", formats=["json"], images="base64")
parts = []
for node in result.iter_nodes():
    if node.type == "visual":
        if node.asset_id and node.availability == "available":
            asset = result.document["assets"][node.asset_id]
            parts.append({"kind": "image", "data_uri": asset["data_uri"], "node_id": node.id})
        else:
            parts.append({"kind": "unavailable_visual", "node_id": node.id})
    else:
        parts.append({"kind": "text", "text": node.content, "node_id": node.id})
```

`parts` is application-owned intermediate data, not a model-provider request
schema. Adapt it to your provider and preserve table/list structure when needed.
No fixed text/image alternation is assumed; preserve extracted reading order.

### Existing jobs and explicit storage

```python
def convert_job(pdf_path, unique_export_directory):
    result = parse(pdf_path, formats=["json", "chunks"], strict=True)
    result.save(unique_export_directory)
    return result.model_dump(mode="json")
```

Run this inside your existing job system. Choose a new directory per run. The
library does not own queues, durable job state, upload storage or result databases.
Critical ingestion should validate domain data as well as extraction status.

## Schemas and integration boundaries

```python
from trueparse import get_schema

request_schema = get_schema("request")
response_schema = get_schema("response")
document_schema = get_schema("document")
chunk_schema = get_schema("chunk")
```

`get_schema()` defaults to `document`; unsupported names raise `ValueError`.
Schemas are bundled JSON Schema Draft 2020-12 dictionaries and load without a
service/network call. CanonicalDocument validation additionally enforces graph
references and semantic invariants that ordinary JSON Schema cannot fully express.

Use the application recipes above for Python integration and the JavaScript
request example in [API References](API_References.md) for HTTP integration.

The current SDK does not promise exact PDF layout recreation, arbitrary chart
series extraction, general handwritten recognition or LaTeX reconstruction.
Quality scores are heuristic diagnostics, not calibrated accuracy probabilities.
Validate your supported document classes before production use; test counts and
schema compliance alone do not establish source fidelity.

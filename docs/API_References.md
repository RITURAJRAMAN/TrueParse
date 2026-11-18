# TrueParse API References

HTTP reference for TrueParse 0.1.3 and document schema 3.0.
This document covers the HTTP service, wire contracts and server operation.
For the local Python library and Python HTTP client, see [SDK References](SDK_References.md).

## Contents

- [Start the service](#start-the-service)
- [Choose an output](#choose-an-output)
- [Endpoints](#endpoints)
- [Submit a PDF](#submit-a-pdf)
- [Request options](#request-options)
- [Profiles and OCR](#profiles-and-ocr)
- [Image delivery and JSON projection](#image-delivery-and-json-projection)
- [Response envelope](#response-envelope)
- [Document JSON](#document-json)
- [Chunks](#chunks)
- [Errors and strict acceptance](#errors-and-strict-acceptance)
- [Resource limits](#resource-limits)
- [Server configuration](#server-configuration)
- [Integration recipes](#integration-recipes)
- [Operation and troubleshooting](#operation-and-troubleshooting)
- [Durable jobs and batches](#durable-jobs-and-batches)

## Start the service

Install TrueParse 0.1.3 from the repository:

```bash
pip install ".[api]"
trueparse serve --host 127.0.0.1 --port 8000
```

For scanned PDFs, install `pip install ".[api,ocr]"` instead. Exact chunk token
counting additionally requires the `tokenizer` extra and provisioned encoding data.
Native PDFs do not require the OCR extra.

For an ASGI process manager, the application import string is
`trueparse.api.app:app`, for example:

```bash
python -m uvicorn trueparse.api.app:app --host 127.0.0.1 --port 8000
```

The base URL in these examples is `http://127.0.0.1:8000`.
Shell examples use POSIX quoting; on Windows, use `curl.exe` with shell-appropriate
quoting, or the JavaScript example below.

Direct parsing is synchronous request/response and does not persist uploads.
Optional job/batch endpoints persist inputs and results with bounded retention.
Responses are JSON
envelopes even when only Markdown or text is requested. Outputs are not streamed
as they are extracted.

## Choose an output

| Application need | Request | Reason |
| --- | --- | --- |
| Readable LLM input | `formats:["markdown"]` | Headings, lists and readable tables |
| Plain search/indexing text | `formats:["text"]` | Reading-order wording with deterministic separators |
| Application records/tables | `formats:["json"]` | Typed nodes, cells, references and provenance |
| Semantic browser content | `formats:["html"]` | HTML headings, lists, tables and figures |
| Retrieval/RAG | `formats:["chunks"]` | Chunk text with source spans and token accounting |
| Multimodal application input | `formats:["json"], images:"base64"` | Ordered visual occurrences and embedded asset bytes |
| Several consumers | Combine formats in one request | One extraction supplies the requested views |

Markdown, text and semantic HTML preserve extracted content and logical order;
they do not reproduce PDF fonts, pagination or exact geometry. Image bytes do
not provide semantic descriptions of charts. Table cells and OCR results still
need accuracy validation on your document types.

## Endpoints

| Method | Path | Authentication | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | None | Version and backend availability |
| POST | `/v1/parse` | `X-API-Key` when configured | Convert one uploaded PDF |
| GET | `/docs` | None | Interactive OpenAPI documentation |
| GET | `/redoc` | None | Alternative generated API documentation |
| GET | `/openapi.json` | None | Machine-readable HTTP schema |
| POST | `/v1/jobs` | Configured API key | Submit a durable job; 202 |
| GET | `/v1/jobs/{job_id}` | Configured API key | Status/progress |
| GET | `/v1/jobs/{job_id}/result` | Configured API key | Successful ParseResult |
| POST | `/v1/jobs/{job_id}/cancel` | Configured API key | Cancellation request |
| POST | `/v1/batches` | Configured API key | Submit multiple PDFs; 202 |
| GET | `/v1/batches/{batch_id}` | Configured API key | Aggregate status and paginated children |
| POST | `/v1/batches/{batch_id}/cancel` | Configured API key | Cancel unfinished children |

### GET /health

```bash
curl http://127.0.0.1:8000/health
```

Example response:

```json
{"status":"ok","version":"0.1.3","ocr_available":false,"auth_required":false}
```

`ocr_available` reports backend availability, not a per-document accuracy test.
`auth_required` indicates whether parsing requires a key. The health endpoint
itself remains public.
If the managed job scheduler stops unexpectedly, health and job endpoints return
503 `WORKER_UNAVAILABLE`. Restart the API to resume queued work.

### POST /v1/parse

Accepts one PDF using either multipart form data or a raw PDF body. Successful
extractions return HTTP 200, including `status:"partial"` when strict mode is off.
This endpoint has no server-path, URL-fetch or batch request mode. Use the
dedicated job/batch endpoints for background work and polling.

## Submit a PDF

### Multipart form

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `file` | Uploaded file | Yes | PDF bytes and original filename |
| `options` | String containing a JSON object | No | Parsing options; defaults to `{}` |

Only these field names are accepted. The parser permits at most one file part
and one ordinary field. Options are limited to 16,384 characters; upstream HTTP
servers may impose additional transport limits.

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/parse \
  -F 'file=@report.pdf;type=application/pdf' \
  -F 'options={"formats":["markdown","json"],"images":"none"}'
```

Let your HTTP library generate the multipart boundary. Do not manually set
`Content-Type: multipart/form-data` without a boundary.

### Raw PDF body

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/parse \
  -H 'Content-Type: application/pdf' \
  -H 'X-Filename: report.pdf' \
  -H 'X-Parse-Options: {"formats":["text","json"],"ocr":"never"}' \
  --data-binary @report.pdf
```

`X-Filename` defaults to `document.pdf`. `X-Parse-Options` defaults to `{}` and
has the same 16,384-character limit. Proxies commonly have smaller header limits;
use multipart for large option objects. Send binary PDF bytes, not a base64
string or a JSON object containing a filename.

### Authentication

When the operator sets `TRUEPARSE_API_KEY`, add:

```text
X-API-Key: your-key
```

A missing or incorrect key returns HTTP 401. Without a configured key, parsing
does not require authentication. This service does not issue access tokens or
provide a user-management API.

## Request options

All keys are optional. Unknown keys and invalid values return `INVALID_OPTIONS`
with HTTP 422. Duplicate format names are collapsed, preserving their order.

| Key | Accepted values/type | Default | When to use |
| --- | --- | --- | --- |
| `formats` | Nonempty array of `markdown`, `json`, `html`, `text`, `chunks` | `["markdown"]` | Select deliverables |
| `images` | `none`, `base64` | `none` | Control visual bytes across exports; `path` is rejected over HTTP |
| `profile` | `fast`, `balanced`, `accurate`, `maximum_accuracy` | `balanced` | Choose extraction effort |
| `ocr` | `auto`, `always`, `never` | `auto` | Control local text recognition |
| `password` | String or null | null | Unlock encrypted PDFs |
| `max_pages` | Positive integer or null | null | Process only the first N pages |
| `timeout_seconds` | Positive number or null | Server deadline | Request a shorter worker deadline |
| `strict` | Boolean | false | Reject known incomplete extraction |
| `limits` | Resource-limit object | Default limits capped by server | Reduce per-request resource ceilings |
| `chunk_strategy` | `hybrid`, `token`, `section` | `hybrid` | Select chunk boundaries |
| `chunk_size` | Integer >= 32 | 512 | Token budget for token/hybrid chunks |
| `chunk_overlap` | Integer >= 0 | 64 | Retain text context across chunks |
| `chunk_tokenizer` | `approximate`, `cl100k_base`, `o200k_base` | `approximate` | Select chunk token counter |
| `json_options` | Projection object | See below | Select document JSON fields only |

Chunk options take effect when `chunks` is requested. JSON projection options
affect `data.json`, not Markdown, HTML, text or chunks. `max_pages` is a deliberate
scope selection, not an extraction failure; pages beyond the selected prefix
are not processed. There is no arbitrary page-range selector.

Example options object:

```json
{
  "formats": ["json", "markdown", "chunks"],
  "images": "base64",
  "profile": "balanced",
  "ocr": "auto",
  "max_pages": 20,
  "timeout_seconds": 90,
  "strict": false,
  "limits": {"max_response_bytes": 33554432},
  "chunk_strategy": "hybrid",
  "chunk_size": 512,
  "chunk_overlap": 64,
  "chunk_tokenizer": "approximate",
  "json_options": {"coordinates": true, "tables": {"grid": true, "markdown": false, "html": false}}
}
```

## Profiles and OCR

Profiles choose heuristics; their names are not measured accuracy guarantees.

| Profile | Render DPI | Table strategies | Span reconstruction / continuation / paragraph merge | Use |
| --- | ---: | --- | --- | --- |
| `fast` | 96 | Lines | Disabled | Simple native PDFs and latency-sensitive previews |
| `balanced` | 150 | Lines | Enabled | General starting point |
| `accurate` | 200 | Lines, then text | Enabled | More difficult or unruled tables |
| `maximum_accuracy` | 300 | Lines, then text | Enabled | Higher render/OCR effort after testing resource cost |

`ocr:"auto"` uses scan/region heuristics and native-character thresholds of 30,
80 and 200 for balanced, accurate and maximum_accuracy respectively. The fast
profile disables automatic OCR; explicitly selecting `always` overrides this.

- `never`: native extraction only. Image-only text cannot be recovered through OCR.
- `auto`: invoke local OCR where the pipeline identifies a need and a backend is available.
- `always`: request OCR regardless of the automatic page trigger; increases work.

The OCR extra must be installed on the server. Prepare backend models before
offline deployment; parsing uses local recognition rather than a remote model API.
Unavailable or unsuccessful recovery is surfaced through warnings/issues and
may produce partial extraction. `strict:true` rejects known incomplete results.
OCR does not imply full semantic recovery of scanned tables, charts or handwriting.

## Image delivery and JSON projection

### Visual bytes

| `images` | Behavior | Use |
| --- | --- | --- |
| `none` | No visual payloads; occurrence records remain in ordinary JSON | Text-centric processing and smaller responses |
| `base64` | Embed data URIs in JSON/assets and applicable Markdown/HTML | Portable images and multimodal consumption |

Plain text cannot embed binary images. An omitted visual is different from an
unavailable visual: omission was requested; unavailability means requested bytes
could not be supplied. Several visual occurrences may reference the same asset.
An `asset_id` may be null when no asset record exists.

### JSON projection

| `json_options` key | Default | Effect |
| --- | --- | --- |
| `coordinates` | false | Include available node/cell bounding boxes |
| `metadata` | true | Include PDF metadata separately from body content |
| `hierarchy` | true | Include section hierarchy |
| `quality_scores` | false | Include diagnostic quality metrics |
| `relationships` | true | Include resolvable relationships |
| `text_only` | false | Exclude visual nodes/assets from document JSON; retain text/list/table nodes |
| `tables.grid` | true | Include structured cells |
| `tables.markdown` | true | Include a table's Markdown representation |
| `tables.html` | false | Include a table's HTML representation |

The `tables` key accepts an object or Boolean. `tables:true` is shorthand for default table options. `tables:false` disables
grid, Markdown and HTML fields; it does not remove table nodes or their content.
Prefer the object form for explicit configuration.

For **JSON without images**, use `formats:["json"], images:"none"`. This retains
visual occurrence metadata. Add `json_options:{"text_only":true}` when the JSON
must exclude those visual records too. This does not convert pictures into
descriptions; OCR may recover readable text, not arbitrary image meaning.

## Response envelope

All selected outputs appear under `data`. Unrequested outputs are null.

```json
{
  "document_id": "doc_example",
  "source_file": "report.pdf",
  "status": "completed",
  "formats_provided": ["text"],
  "data": {"json": null, "markdown": null, "html": null, "text": "Revenue increased.", "chunks": null},
  "metrics": {"duration_ms": 123, "page_count": 1, "asset_count": 0},
  "warnings": [],
  "issues": [],
  "scope": {"source_pages": 1, "requested_pages": [1], "processed_pages": [1], "failed_pages": []},
  "representations": {"text": {"rendered_node_ids": ["p1"], "excluded_node_ids": [], "losses": []}}
}
```

IDs and content above are illustrative. Actual metrics include:

| Field | Meaning |
| --- | --- |
| `document_id` | Source-document identity |
| `source_file` | Normalized source filename |
| `status` | `completed` or `partial`; check independently of HTTP status |
| `formats_provided` | Requested distinct formats |
| `data` | Requested output strings/document/chunk records |
| `warnings` | Human-readable diagnostic messages |
| `issues` | Structured extraction findings |
| `scope` | Original page count and requested/processed/failed page numbers |
| `representations` | Per-format rendered/excluded node IDs and representation losses |
| `metrics.duration_ms` | Wall duration including supervised worker overhead |
| `metrics.page_count` | Number of page records in the selected result |
| `metrics.asset_count` | Number of extracted asset records, not occurrence count |
| `metrics.resource_usage` | Rendered pixels, unique OCR pages and asset bytes accounted by the pipeline |
| `metrics.stage_timings_ms` | Timings for performed stages |
| `metrics.stage_calls` | Invocation counts for performed stages |

Nested stage timings overlap; their sum need not equal wall duration. Metrics
are diagnostics, not a process-memory measurement or an accuracy certificate.

An issue contains `code`, `stage`, `message`, `severity` (`info`, `warning`,
`error`), optional `page`, `node_ids`, and `recovered`. Status becomes partial
when pages fail or an unrecovered non-info issue exists. Recovered findings may
remain visible without making status partial. Known extraction issues and
representation losses are separate: deliberate image omission is not an error.

## Document JSON

`data.json` is a structured object, not a JSON-encoded string. It uses schema 3.0.
The schema artifacts are [request](../src/contracts/schemas/request-3.0.json),
[response](../src/contracts/schemas/response-3.0.json),
[document](../src/contracts/schemas/document-3.0.json) and
[chunk](../src/contracts/schemas/chunk-3.0.json).
`/openapi.json` includes the request and typed document contracts.

| Document field | Meaning |
| --- | --- |
| `schema_version` | `"3.0"`; separate from package version |
| `document_id`, `extraction_id`, `engine_version` | Source identity, extraction/configuration identity and engine version |
| `source` | `filename`, SHA-256 fingerprint and original `page_count` |
| `scope`, `status`, `issues` | Extraction scope and known failures |
| `metadata` | PDF metadata, when selected |
| `pages` | Page number, width, height, rotation and `node_ids` |
| `nodes` | ID-keyed typed content records |
| `reading_order` | Every selected logical node ID exactly once, in traversal order |
| `assets` | ID-keyed binary asset descriptors |
| `sections` | Section IDs, titles, levels, parent IDs and node references |
| `relationships` | Typed links with `id`, `type`, `source_id`, `target_id` |
| `projection` | `excluded_node_ids` and `omitted_fields` for explicit projection choices |
| `diagnostics` | Document/page heuristic quality details when requested |

Do not rely on JSON object-key order. Traverse `reading_order` and resolve each
ID in `nodes`. For a physical page, use `pages[].node_ids`. Cross-page content
can be one logical node with several source fragments; avoid counting it twice.

### Node records

Every node has `id`, `type`, `role`, `content`, `source`, `method`, and `engine`.
`type` selects the structural shape; `role` provides semantic classification
such as paragraph, section_header, image, chart, caption or unknown. Role names
do not imply that every possible semantic class has a specialized extractor.

| `type` | Additional fields | Consumer behavior |
| --- | --- | --- |
| `text` | `level`, `original_text`, `source_texts`, `normalization_map`, `recovery_reason` | Read `content`; inspect raw mappings when normalization matters |
| `list` | `ordered`, `items` | Recurse through items' `text`, `marker`, `children` |
| `table` | `rows`, `columns`, optional `cells`, `markdown`, `html` | Use cells for structured records; respect spans |
| `visual` | `asset_id`, `availability`, `parent_table_id` | Resolve bytes through assets and retain occurrence position |

Cell fields are `id`, `row`, `column`, `row_span`, `col_span`, `is_header`,
`text`, `kind`, and `source`. Indices start at zero and spans are positive.
`kind:"visual_marker"` denotes an uninterpreted vector annotation, for example
`[TrueParse vector mark]`; do not interpret it as a boolean or recognized symbol.
Visuals inside tables can remain separate nodes linked by `parent_table_id`.

Source fragments contain a one-based `page`, optional `[x0,y0,x1,y1]` `bbox`,
optional `start`/`end`, and optional `source_id`. Coordinates are unrotated PDF
points (1/72 inch), measured from the top left. Page rotation is recorded
separately. Character offsets are Unicode code points with exclusive ends;
JavaScript UTF-16 offsets require conversion when slicing non-BMP characters.

Normalization mappings partition normalized `content`. Each entry gives `start`,
`end` and, for copied text, `source_id`, `source_start`, `source_end` into
`source_texts`. A null `source_id` identifies inserted separator text. These
mappings explain transformations such as joining/dehyphenating paragraphs.

Assets contain `id`, `mime_type`, optional `sha256`, `width`, `height`,
`availability`, optional `data_uri` and optional `path`. HTTP never supplies
local-path delivery. Available HTTP assets use data URIs; omitted/unavailable
assets have no byte representation. Inspect optional fields rather than assuming
all records have all payloads. Node IDs are not promised stable across engine revisions.

## Chunks

Request `formats:["chunks"]` for retrieval-ready records. Also request `json`
when your application needs to resolve chunk element IDs or asset references.

| Strategy | Behavior | Choose when |
| --- | --- | --- |
| `hybrid` | Section-aware grouping followed by token-budget splitting | General retrieval |
| `token` | Token-budget grouping without section boundaries | Uniform-size retrieval units |
| `section` | Section-oriented text without a text token ceiling; tables still use row budgets | Preserving section text matters more than size |

`chunk_overlap` applies to text context; it does not promise identical overlap
for atomic table rows. When overlap is >= size, the chunker reduces it to a
quarter of the size. Prefer explicitly choosing overlap below size.
Tables are emitted separately from surrounding text and may be split between
rows under every strategy, including `section`; a section is not guaranteed to
produce exactly one chunk. Very small exact budgets may also be exceeded by an
indivisible Unicode character.

| Chunk field | Meaning |
| --- | --- |
| `id`, `document_id`, `chunk_index` | Document-scoped deterministic identity and sequence |
| `text` | Chunk representation supplied to your retrieval pipeline |
| `token_estimate`, `tokenizer` | Count and counter used; exact only for the selected exact encoding |
| `budget_exceeded` | An atomic unit, such as a table row, exceeded the selected budget |
| `source_spans` | Element/source element IDs, page, optional box, content offsets or row range, representation |
| `section_id`, `section_path` | Section context and heading breadcrumb |
| `page_start`, `page_end`, `bboxes` | Source page range and spatial references |
| `element_ids`, `element_types`, `asset_ids` | Content/asset references |

Text spans index canonical element content; table row ranges use exclusive
`row_end`. Table-rendered text is not a raw substring of paragraph content.
Chunks are not embeddings and do not include a vector database or LLM call.

The default `approximate` counter is suitable when exact model token counts are
unnecessary. For `cl100k_base`/`o200k_base`, the operator must install the tokenizer
extra and provision encoding data. These counts exclude your prompts, message
envelopes, image tokens and the downstream model's output budget.

## Errors and strict acceptance

Engine errors have this shape; context values can be null:

```json
{
  "error": {
    "code": "PARSE_TIMEOUT",
    "message": "Parsing exceeded the configured limit.",
    "request_id": null,
    "document_id": null,
    "page": null,
    "element_id": null,
    "retryable": true,
    "details": {}
  }
}
```

Authentication and other handled HTTP exceptions may include only `code` and
`message`. Framework-generated 404/405 or unexpected server errors can have a
different body. Handle HTTP status and tolerate non-JSON proxy/server failures.

| HTTP | Typical code | Meaning/action |
| --- | --- | --- |
| 400 | `INVALID_PDF` | Missing usable PDF structure or unreadable document; check input |
| 401 | `AUTHENTICATION_FAILED` | Supply the configured `X-API-Key` |
| 403 | `PDF_PASSWORD_INCORRECT` | Correct the PDF password |
| 413 | `UPLOAD_TOO_LARGE`, `PDF_RESOURCE_LIMIT` | Upload or processing/response limit exceeded; reduce work or ask operator to adjust ceilings |
| 415 | `INVALID_OPTIONS` | Send multipart or `application/pdf` |
| 422 | `INVALID_OPTIONS` | Correct request keys, values or server-cap violations |
| 422 | `PDF_PASSWORD_REQUIRED` | Supply a password |
| 422 | `EXTRACTION_INCOMPLETE` | Strict mode rejected known incomplete extraction; inspect `details.issues` |
| 422 | Other engine codes | Parsing, OCR, rendering, asset or serialization failure |
| 504 | `PARSE_TIMEOUT` | Hard worker deadline expired |

With `strict:false`, usable partial results can return 200. With `strict:true`,
known unrecovered warning/error issues or failed pages cause rejection. Strict
mode cannot detect every extraction omission and does not certify perfect fidelity.

Use bounded retries for transient transport/deadline failures only when your
application permits the extra processing cost. Invalid options/passwords are
not fixed by retrying unchanged. No idempotency-key or result-cache API exists.

## Resource limits

All limit values are positive integers. Byte values use bytes, not MB strings.

| `limits` key | Default | Scope |
| --- | ---: | --- |
| `max_render_pixels` | 25,000,000 | Single rendered region/image dimension guard |
| `max_total_render_pixels` | 100,000,000 | Accumulated requested render pixels |
| `max_ocr_pages` | 200 | Unique pages requiring OCR rendering |
| `max_asset_bytes` | 67,108,864 | Accounted extracted visual bytes |
| `max_response_bytes` | 134,217,728 | Serialized result size |
| `max_nodes` | 200,000 | Extracted logical content count before JSON projection |
| `max_table_cells` | 250,000 | Extracted cell count and declared grid-size checks |

These are work/output guards, not an exact heap-memory quota. Base64 expansion
counts toward response size. Limits fail explicitly rather than truncating output.
The underlying parsing path also has a 200 MiB PDF input ceiling; raising only
`TRUEPARSE_MAX_UPLOAD_MB` does not remove that ceiling.

The server validates explicitly supplied limits against operator ceilings.
Effective limits are the minimum of request/default values and server ceilings.
Omitting a field uses its normal default, capped by the server; a raised operator
ceiling does not automatically raise an omitted client default.

## Server configuration

Set environment variables before starting/restarting the process.

Authentication is opt-in for every bind address. Setting `TRUEPARSE_API_KEY`
requires the matching header on parsing and job/batch endpoints. An unset key
also permits requests when the server binds beyond loopback.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRUEPARSE_API_KEY` | Unset | Require `X-API-Key` for parsing |
| `TRUEPARSE_CORS_ORIGINS` | Unset | Comma-separated allowed browser origins |
| `TRUEPARSE_MAX_UPLOAD_MB` | 200 | Stream-enforced upload ceiling in MiB; minimum 1 |
| `TRUEPARSE_TIMEOUT_SECONDS` | 120 | Default and maximum worker deadline in seconds; minimum 1 |
| `TRUEPARSE_RESOURCE_LIMITS` | `{}` | JSON object overriding resource ceilings |

`TRUEPARSE_MAX_UPLOAD_MB` must be a positive integer. Explicit empty, nonnumeric,
zero and negative values fail API startup before job storage is initialized.
| `TIKTOKEN_CACHE_DIR` | Library cache location | Shared setup/runtime exact-encoding cache |
| `DATA_GYM_CACHE_DIR` | Unset | Fallback encoding cache when `TIKTOKEN_CACHE_DIR` is unset |
| `TRUEPARSE_TOKENIZER_DIR` | Unset | Directory of named `cl100k_base.tiktoken` / `o200k_base.tiktoken` files |
| `TRUEPARSE_TOKENIZER_FILE` | Unset | Explicit single-encoding data file |

For example, in PowerShell:

```powershell
$env:TRUEPARSE_API_KEY = 'replace-with-your-key'
$env:TRUEPARSE_TIMEOUT_SECONDS = '300'
$env:TRUEPARSE_RESOURCE_LIMITS = '{"max_response_bytes":67108864,"max_ocr_pages":100}'
trueparse serve --host 127.0.0.1 --port 8000
```

Clients can request a shorter positive timeout, never one above the server's.
The worker deadline covers supervised parsing; upload time, waiting for a slot,
response transport and proxy handling can add to total HTTP duration. Configure
HTTP client/proxy timeouts with headroom.

### Provision exact counters

Use the same persistent cache during setup and service execution:

```powershell
pip install ".[api,tokenizer]"
$env:TIKTOKEN_CACHE_DIR = 'D:/service-cache/trueparse-tokenizers'
python -c "import tiktoken; [tiktoken.get_encoding(n) for n in ('cl100k_base','o200k_base')]"
trueparse serve
```

Provisioning can download data; parsing itself does not download token encodings.
Runtime lookup order is explicit file, explicit directory, `TIKTOKEN_CACHE_DIR`,
`DATA_GYM_CACHE_DIR`, then the OS temporary `data-gym-cache` directory. Prefer a
persistent operator-managed location rather than relying on temporary files.
Missing/mismatched local data or an absent tokenizer package causes an option
error when exact chunks are requested. Encoding choice must match the downstream
model/application's requirements; TrueParse does not select it from a model name.

### CORS and deployment

CORS defaults to disabled. Configured origins allow GET/POST and the
`Content-Type`, `X-API-Key`, `X-Filename`, `X-Parse-Options`, `Idempotency-Key` headers. Cookie-based
credential handling is not configured. An API key embedded in public browser
JavaScript is visible to users; use your own application backend where appropriate.

The CLI binds to localhost by default. For container deployment,
`docker compose up --build` uses [the Compose configuration](../docker-compose.yml).
The [Dockerfile](../Dockerfile) supports `INSTALL_OCR=false`; its normal image
prepares OCR during build. Exact tokenizer data is a separate setup step.
The supplied container configuration runs non-root with a read-only filesystem,
temporary memory storage and a persistent job volume. Local Docker checks cover
managed API jobs, mixed batches, scanned-PDF OCR, restart persistence and shutdown.
Validate sustained workloads and operational limits
on your deployment platform.

## Integration recipes

### Browser or Node.js multipart request

This browser example receives a `File` from a file picker:

```javascript
async function convertPdf(file, apiKey) {
  const form = new FormData();
  form.append("file", file);
  form.append("options", JSON.stringify({
    formats: ["json", "markdown"],
    images: "none",
    json_options: { coordinates: true }
  }));
  const response = await fetch("http://127.0.0.1:8000/v1/parse", {
    method: "POST",
    headers: apiKey ? { "X-API-Key": apiKey } : {},
    body: form
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message ?? `HTTP ${response.status}`);
  if (body.status === "partial") console.warn(body.issues);
  for (const id of body.data.json.reading_order) {
    const node = body.data.json.nodes[id];
    console.log(node.type, node.content);
  }
  return body;
}
```

Production HTTP handling should also tolerate non-JSON errors and configure
application-specific request cancellation/timeouts. Browser access requires the
operator to allow the application's origin.

### Structured tables

```json
{"formats":["json"],"images":"none","json_options":{"tables":{"grid":true,"markdown":false,"html":false}}}
```

Traverse table nodes; use each cell's row/column and spans rather than splitting
Markdown. A visual mark or empty cell is not automatically a boolean or null
business value. Your application owns domain-specific validation and typing.

### Multimodal content

```json
{"formats":["json"],"images":"base64"}
```

Traverse reading order. For visuals, resolve `asset_id` and inspect availability
before reading `data_uri`; for other nodes use content or structured cells.
Build your model provider's message format in the application. No fixed
alternation of text and images is assumed.

### Strict ingestion

```json
{"formats":["json","chunks"],"strict":true,"ocr":"auto","chunk_size":768,"chunk_overlap":96}
```

Reject failed requests, then apply domain-specific checks before indexing.
Store original source identity and source spans for citations. A successful
strict response still requires your application to validate critical amounts,
dates and row/column associations.

## Operation and troubleshooting

- **Partial response:** inspect `issues`, `scope.failed_pages`, visual availability
  and representation losses; HTTP 200 alone is insufficient.
- **Empty text on a scan:** install/prepare OCR, use a non-fast profile or explicit
  `ocr:"always"`, and inspect recovery issues. Image bytes alone are not OCR text.
- **413 with small input:** processing or response budgets can fail even when
  upload size is small. Reduce images/formats/page scope or adjust operator limits.
- **504 on a long report:** raise the operator deadline where appropriate and
  leave HTTP timeout headroom; increasing a client timeout alone does not raise it.
- **Missing boxes:** request `json_options.coordinates:true`; not every recovery
  path necessarily provides a box.
- **Missing outputs:** check `formats`; unrequested outputs remain null.
- **Repeated assets:** distinguish deduplicated asset bytes from page occurrences.
- **Exact counter fails:** confirm installed extra, encoding name, file checksum
  and service account access to the provisioned cache.

Two requests per server process may buffer/parse concurrently. Additional
requests wait for a slot; this is not a durable queue or a rate-limit API.
Timed-out parsing workers are terminated and joined. Multiple server processes
have separate concurrency slots and increase total memory/CPU demand.

Applications can use the optional durable service or their existing queues.
Applications own indexes and access control around exported results. The service does not upload PDFs to a hosted
model. Conversion remains heuristic: passing schema validation and receiving
`completed` does not prove every source item was recovered correctly.

## Durable jobs and batches

Jobs and batches are enabled by default. Start the API once; it owns the job
scheduler and isolated parsing processes, including startup and shutdown:

```powershell
$env:TRUEPARSE_JOBS_ROOT = 'D:/trueparse-job-data'
trueparse serve
```

The job root defaults to `data/jobs` and must be writable. Startup initializes
SQLite metadata; submitted jobs stream inputs to staging and publish result JSON
atomically. Parsing work runs in bounded isolated processes. Shutdown stops
admission and interrupts outstanding work for recovery on the next API startup.
Set `TRUEPARSE_JOBS_ENABLED=false` to disable these endpoints with 503
`JOBS_DISABLED` and avoid job storage initialization. Direct parsing remains in
memory. The store supports local single-host storage. The configured shared API
key grants access to the whole store. Operators control tenant isolation.

### Submission

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/jobs \
  -H 'Idempotency-Key: report-001' \
  -F 'file=@report.pdf' \
  -F 'options={"formats":["json","markdown"],"images":"base64","strict":true}'

curl --fail-with-body http://127.0.0.1:8000/v1/batches \
  -F 'files=@first.pdf' -F 'files=@second.pdf' \
  -F 'options={"formats":["text","json"]}'
```

Jobs accept one multipart `file`; batches accept repeated `files`. Both accept
one optional JSON `options` field using ParseRequest and normal server ceilings.
Raw PDF bodies and path-based image delivery are not supported for submissions.
Uploads stream to managed staging; acknowledgement occurs only after durable
admission. A 202 response means accepted, not successfully extracted.

Invalid transport/shared options, upload limits or queue capacity reject the
whole submission and clean staging. After admission, invalid PDFs, missing
passwords and extraction failures affect only the respective child. Duplicate
filenames are allowed and never share storage paths.

### Status, results and pagination

| JobStatus field | Meaning |
| --- | --- |
| `job_id`, `batch_id` | Opaque job identifier and optional parent |
| `input_index`, `filename` | Submission order and normalized name |
| `state` | queued/running/cancelling/succeeded/failed/cancelled/expired |
| `created_at`, `started_at`, `finished_at`, `expires_at` | Unix seconds; null until known |
| `attempt`, `max_attempts` | Attempt count and interruption limit |
| `progress` | stage/current_page/total_pages; not a linear percentage |
| `document_id` | Available after successful publication |
| `error` | Optional code/message/retryable/details/page/element_id |
| `status_url`, `result_url` | Relative polling/result URLs |

GET the result URL after `succeeded`. It returns the existing ParseResult
envelope. Successful execution may contain `status="partial"` when strict is
false; strict rejection is a failed job. No raw artifact/asset download routes
are added; consume the envelope and embedded images.

Result retrieval returns 200 when successful, 409 `JOB_NOT_READY` otherwise,
410 `JOB_EXPIRED` for expired results, and 404 `JOB_NOT_FOUND` after purge or for
unknown IDs. Unreadable stored results return 500 `RESULT_UNAVAILABLE`.
Status queries return `state="expired"` during tombstone retention.

BatchStatus contains `batch_id`, `state`, `total`, `counts` by child state,
`jobs`, `next_cursor`, and `status_url`. GET supports `cursor` (default 0) and
`limit` (default 50, range 1–100). Use the returned next_cursor for continuation.
Aggregate states are queued/running/succeeded/failed/cancelled/mixed/expired.
Retrieve individual child results rather than expecting large payloads in status.

### Cancellation, recovery and retry safety

POST `/cancel` to cancel a job or unfinished batch children. Queued jobs cancel
immediately; running jobs enter cancelling (202) until their subprocess stops.
Repeated requests are safe. Terminal jobs retain their state (200). Completion
and cancellation are resolved transactionally; a stale attempt cannot publish.

Independent heartbeats renew worker leases. Interrupted executions requeue up
to MAX_ATTEMPTS; invalid PDFs, options and strict failures are not automatically
retried. Processing is potentially at-least-once, with fenced publication.
Shutdown stops claims and terminates/joins children before releasing leases.
Child processes watch their parent to avoid indefinite orphan execution.
Heartbeat exceptions are logged with the job ID and traceback. The affected
process is stopped and its lease is released for bounded interruption recovery.
Cancellation requests retain their cancelled state. When storage is unavailable
during shutdown, cleanup continues for the other owned processes and logs the
failure; persisted leases expire for recovery after storage becomes available.

OCR uses bounded ONNX session pools with two intra-operation threads and one
inter-operation thread. Successful supplemental OCR probes that find no text in
an image region on an otherwise readable native page produce an informational
`OCR_NO_TEXT` issue. OCR initialization, recognition and rendering failures remain
incomplete extraction. Unrecovered scanned text also remains incomplete. Strict
mode follows these completeness diagnostics.

Optional `Idempotency-Key` (1–128 characters) returns the original IDs for
identical bytes, names and effective options. Reuse with different inputs returns
409 `IDEMPOTENCY_CONFLICT`. Keys are store-wide and survive until record purge.
Options are included through a keyed digest; fingerprints contain no plaintext
password. Without a key, each submission is independent. Clients do not retry
submissions automatically.

### Settings and persistence

Configure these `TRUEPARSE_JOBS_` suffixes on the API service:

| Suffix | Default | Meaning |
| --- | --- | --- |
| ENABLED | true | Enable background jobs and batches with managed execution |
| ROOT | data/jobs | Writable local store |
| WORKERS | 2 | Global active-job cap in this store |
| QUEUE_CAPACITY | 100 | Queued/running/cancelling capacity |
| MAX_BATCH_FILES | 20 | Files per batch |
| MAX_UPLOAD_BYTES | 209715200 | Aggregate bytes and staging reservation per upload |
| DISK_QUOTA_BYTES | 2147483648 | Managed payload bytes and reservations |
| RETENTION_SECONDS | 86400 | Result lifetime after terminal state |
| TOMBSTONE_SECONDS | 86400 | Additional expired-record lifetime |
| HEARTBEAT_SECONDS | 5 | Heartbeat/maintenance cadence |
| LEASE_SECONDS | 30 | Must exceed twice heartbeat interval |
| STAGING_SECONDS | 300 | Abandoned upload lease |
| MAX_ATTEMPTS | 2 | Total attempts after interruption |

The normal API upload ceiling and parser's 200 MiB ceiling apply per file.
Staging reserves MAX_UPLOAD_BYTES before receiving content; even a small request
can receive 507 `JOB_STORAGE_FULL` when this reservation cannot fit. Claimed jobs
reserve their maximum response size and fail explicitly if unavailable. Queue
saturation returns 429 `JOB_QUEUE_FULL`; uploads exceeding size return 413.

Quota covers managed input/result bytes and reservations, not SQLite/WAL metadata,
filesystem overhead or total RAM. Leave host disk headroom and use container/OS
resource limits. Output is never silently truncated to fit.

Terminal jobs clear options/passwords and clean input files. Results/errors may
contain source content and expire after the configured interval. Tombstone expiry
removes records and idempotency keys. The running API performs periodic retention;
status/result reads also trigger maintenance. Deletion is logical, not a secure
erasure guarantee for disks, WAL or backups. Operators own permissions, encryption
and backups. Direct `/v1/parse` still does not persist input or output.

### Docker

```bash
docker compose up --build
```

The single Compose file starts one API container, including managed background
processing, with a named volume mounted at `/app/job-data`. It runs non-root with
a read-only root filesystem and writable job storage/temporary mounts. Exact
tokenizer data must be provisioned when requested. `docker compose down` preserves
the volume; use `down -v` only when deleting persisted job data is intended.

# Optional jobs, batches and multi-PDF SDK implementation plan

Status: planned only. No functionality described as proposed below is implemented.
Baseline: `9c541cd`, TrueParse 0.2.0 developer preview, document schema 3.0.

## Objective and boundaries

Restore convenient background and multi-document integration without replacing
the extraction engine. Keep `POST /v1/parse`, root SDK imports, ParseRequest,
ParseResult and existing output semantics. Add optional durable service execution
and bounded local multi-PDF helpers around the same parser.

Direct parsing stays memory-only unless the caller explicitly saves a result.
Durable jobs necessarily persist uploaded inputs, options and results temporarily.
Make this distinction visible in configuration and documentation. Do not introduce
remote model calls, a new parser, a vector database or another broad reorganization.

## Verified comparison with v0.1.2

Compared the actual Git tag's API routes, worker manager and SQLite job store.

| v0.1.2 capability | Current baseline | Planned treatment |
| --- | --- | --- |
| `POST /v1/documents/parse` | `POST /v1/parse` | Keep current immediate endpoint |
| `POST /v1/documents/parse-async` | Missing | Restore capability through `POST /v1/jobs` |
| `GET /v1/documents/jobs/{job_id}` | Missing | Restore through `GET /v1/jobs/{job_id}` |
| `POST /v1/batches/parse-async` | Missing | Restore through `POST /v1/batches` |
| `GET /v1/batches/{batch_id}` | Missing | Restore aggregate status and per-file outcomes |
| Worker progress and SQLite records | Missing | Durable state, throttled progress and independent worker execution |
| Interrupted jobs | Marked failed on manager initialization | Lease-based recovery with bounded attempts |
| Document inspection endpoint | CLI inspection only | Separate follow-up; not part of these six recommendations |
| Format/asset download endpoints | Immediate response + explicit local save | Job result retrieval in this scope; separate artifact endpoints deferred |

The old code used a process pool, with a thread fallback, and an in-process
future registry. Do not copy it wholesale: preserve capabilities while using
current contracts and explicit durable ownership. Cancellation and automatic
retention are new work, not claims of old feature parity. Legacy route aliases
are not required for this fresh project; document the capability mapping.

## 1. Preserve immediate parsing

- Keep `POST /v1/parse` unchanged in behavior, including authentication,
  bounded input, resource ceilings, strict mode and worker deadlines.
- Reuse the validated ParseRequest and the same pipeline for job execution.
- Keep parsing option validation centralized; transport-specific upload handling
  can differ for memory-only and durable requests.
- Jobs are disabled by default. No database, worker or job directory is created
  merely by importing the SDK or calling direct parsing.

Acceptance: existing direct SDK/API tests pass with jobs disabled and enabled;
no new persistent artifacts appear after direct parsing.

## 2. Durable background jobs

Proposed HTTP surface:

| Method/path | Result |
| --- | --- |
| `POST /v1/jobs` | Multipart `file` + JSON `options`; 202 with durable job ID and URLs |
| `GET /v1/jobs/{job_id}` | State, progress, timestamps, attempt count and structured error |
| `GET /v1/jobs/{job_id}/result` | Existing ParseResult envelope when successful |
| `POST /v1/jobs/{job_id}/cancel` | Idempotent cancellation request and current status |

Use opaque generated job IDs, independent of document hash: the same PDF can
have different requests and lifecycles. Acknowledgement with 202 occurs only
after input staging and durable database admission succeed. Unknown/invalid
options are rejected before admission; expensive PDF/content validation runs
in the worker and can produce a failed job.

State transitions:

```text
queued -> running -> succeeded | failed
queued -> cancelled
running -> cancelling -> cancelled
running (expired lease) -> queued | failed (attempt limit)
terminal -> expired (retention elapsed)
```

Execution success and extraction completeness are separate: a job can succeed
with `result.status="partial"` when strict mode is false. Strict rejection is a
failed job with structured EXTRACTION_INCOMPLETE context. Do not invent a job
failure for intentional image omission or page scope.

Status records include job/batch IDs, input index and filename, state, created/
started/finished/expiry times, attempt/max_attempts, progress stage and page counts,
optional document ID, and structured error. Never return persisted passwords.
Progress is stage-aware, not a fabricated linear percentage across unequal stages.

Result retrieval: 200 when successful; 409 while pending or after unsuccessful
termination; 404 for unknown jobs; 410 for expired jobs during tombstone retention.
Cancellation: queued jobs transition immediately; running jobs return 202 while
termination completes; terminal jobs return their unchanged status. Database
compare-and-set decides completion/cancellation races. Never report cancelled
while a worker can still publish a result.

## 3. Multi-PDF batch submission

| Method/path | Result |
| --- | --- |
| `POST /v1/batches` | Repeated multipart `files` + shared JSON `options`; 202 with batch ID and child IDs |
| `GET /v1/batches/{batch_id}` | Aggregate counts/state and paginated child outcomes |
| `POST /v1/batches/{batch_id}/cancel` | Idempotently request cancellation of unfinished children |

Each accepted file is an independent job with its original input index. Preserve
duplicate filenames without overwriting files. One extraction failure must not
abort sibling jobs. Return metadata rather than embedding all result payloads;
clients fetch each child result through the ordinary job endpoint.

Admission is atomic for malformed transport, invalid shared options, excessive
count/size or unavailable queue capacity: reject the batch and clean staged files.
After admission, corrupt/encrypted PDFs fail individually during execution.
Reserve capacity for the entire batch in the same transaction as child creation.

Aggregate state is derived from children: queued, running (including cancellation
in progress), succeeded, failed, cancelled or mixed. Include counts so mixed
outcomes are unambiguous. A partial extraction counts as a succeeded execution.
Batch expiry must not erase still-running child work.

## 4. SDK interfaces

Extend TrueParseClient with typed methods:

- `submit_job(source, filename=None, **options)`
- `get_job(job_id)`, `get_job_result(job_id)`, `cancel_job(job_id)`
- `wait_for_job(job_id, poll_interval=1, timeout=None)`
- `submit_batch(sources, **options)`, `get_batch(batch_id, cursor=None, limit=50)`,
  `cancel_batch(batch_id)`
- Matching asynchronous methods with an `a` prefix, documented consistently.

Polling timeout only stops waiting; cancellation requires an explicit call.
Do not automatically retry POST submissions unless an idempotency key is supplied.
Typed job/batch envelopes are separate from document schema 3.0. Successful result
retrieval returns the existing ParseResult with all current helpers.

Local helpers require no API server or durable store:

- `parse_many(sources, *, max_workers=2, ordered=True, **options)` yields typed
  per-file outcomes lazily; each includes input_index, filename and result or error.
- `aparse_many(...)` is an async iterator, consumed with `async for`.
- Use bounded isolated processes, not unbounded threads around PyMuPDF's lock.
  Limit both in-flight submissions and completed-result buffering; ordered mode
  must apply backpressure when an earlier input is slow.
- Consume source iterables lazily. Close/early exit/cancellation must clean up
  outstanding processes. Preserve per-file limits and deadlines.
- Default to collecting per-file failures rather than aborting other files.
  Invalid shared configuration fails before starting any worker.

Before coding, freeze signatures, outcome models, iterator-close semantics and
process ownership in tests. Avoid nesting a supervision process inside another
worker process solely to enforce the same deadline.

## 5. Reliability, storage and execution design

### Packages and ownership

| Location | Responsibility |
| --- | --- |
| `contracts/jobs.py` | Public job/batch/status/outcome models |
| `jobs/store.py` | SQLite persistence, atomic claims, transitions and migrations |
| `jobs/service.py` | Admission, status aggregation and cancellation policy |
| `jobs/worker.py` | Scheduler, leases, isolated workers, deadlines and progress |
| `jobs/retention.py` | Expiry, tombstones and abandoned staging cleanup |
| `io/job_storage.py` | Contained input/result storage and atomic publication |
| `api/jobs.py`, `api/batches.py` | Authenticated transport adapters |
| `sdk/batch.py`, existing SDK client | Local iterators and remote convenience methods |
| `runtime/execution.py` | Shared supervised execution primitives |

Names above are proposed modules, not files already present. Keep orchestration
out of routes and persistence out of extraction. Reuse current error handling,
path containment and request limits where their semantics apply.

### Storage and deployment

Start with SQLite WAL plus local filesystem storage for a single-host deployment.
Use a separate explicitly started worker command, not a scheduler per ASGI import.
Multiple local worker processes may share the store using atomic job leases;
multi-host/network-filesystem operation is outside the first release scope.
Keep a replaceable store interface without implementing Redis/Postgres now.

Use server-generated paths under a configured writable root. Stream uploads to
temporary files, fsync/rename as appropriate, then admit records transactionally.
Filesystem and SQLite cannot share one transaction: define orphan reconciliation
for crashes between staging, admission, result writing and terminal publication.
Publish completed results atomically; readers must not see partial JSON.

Workers atomically claim a job with a lease/fencing token. Heartbeat separately
from progress. Accept completion only from the current token; an expired worker
cannot overwrite a replacement attempt. Requeue interrupted work within a fixed
attempt limit; never retry invalid PDFs, invalid options or strict failures
automatically. Execution may be at-least-once; terminal publication is fenced.

Cancellation terminates and joins the owned process. Shutdown stops new claims,
allows a bounded grace period, then records/reconciles interrupted work. Never
silently fall back to unsafe concurrent in-process parsing.

### Proposed initial settings

| Setting | Proposed default |
| --- | --- |
| Jobs enabled | false |
| Job root | `data/jobs`, explicitly writable when enabled |
| Worker concurrency | 2 per host scheduler configuration |
| Nonterminal queue capacity | 100 jobs |
| Maximum batch files | 20 |
| Maximum total batch upload | 200 MiB; per-file current input ceiling also applies |
| Managed input/result disk quota | 2 GiB, accounted atomically across admission/writes |
| Result retention | 24 hours after terminal state |
| Expiry tombstones | Additional 24 hours |
| Heartbeat / lease | 5 seconds / 30 seconds |
| Maximum attempts after interruption | 2 total |

These defaults require resource testing before release. Reuse current parsing
deadline/resource ceilings; do not silently increase them for jobs. Queue/disk
admission must reserve capacity under concurrency, including result publication.
Define behavior when a running result cannot fit: explicit storage failure and
cleanup, never successful state with a missing result.

Delete input bytes and sensitive options promptly after terminal processing;
retain results/errors for the configured interval. Persist encrypted-PDF passwords
only as long as necessary for execution/recovery, restrict storage permissions,
and exclude them from logs/status/errors. No unconditional promise of encryption
at rest: document operator-owned volume protection.

Require the configured API key on all job/batch reads, writes and cancellation.
The existing shared API key defines one trusted workspace; multi-tenant ownership
is not provided by random job IDs. Add an ownership model before exposing this
as a multi-tenant service.

Support optional Idempotency-Key for submissions with content/options fingerprints,
atomic uniqueness and bounded retention. Identical retries return the original
IDs; changed content under the same key returns 409. Do not persist raw credentials
inside fingerprints. Without a key, repeated submissions are independent jobs.

## 6. Verification and documentation

Implement and validate in this order:

1. Contracts/state machine: request validation, status/error semantics, scope and
   idempotency rules; publish separate schemas/OpenAPI models.
2. Store/storage: transactional admission, capacity, leases, fencing, migrations,
   filesystem crash reconciliation and retention using a controllable clock.
3. Worker: shared parser integration, throttled progress, hard deadline,
   cancellation and restart recovery; prove no orphan workers remain.
4. HTTP jobs then batches: authenticated endpoints, bounded streaming intake,
   polling/result retrieval and atomic batch admission.
5. SDK client methods then local iterators: result typing, lazy consumption,
   bounded buffering, deterministic ordering and early-close cleanup.
6. Integrated lifecycle tests, resource measurements, platform checks and docs.

Required scenarios: native/scanned/encrypted/corrupt/blank PDFs; duplicate names;
partial versus strict extraction; individual batch failures; queue/disk full;
concurrent claim/cancel/complete; worker and API restart; abandoned uploads;
expired/missing results; stale lease completion; repeated idempotency keys;
malformed options; oversized batch; no-worker service; authentication on every
new route; shutdown and SDK iterator cancellation. Include deterministic
fault injection, not timing-sensitive sleeps as the only evidence.

Run current regression tests and add focused job/batch tests. Measure bounded
memory/disk use and sustained throughput; use existing full PDFs for workflow
validation without presenting them as an independently annotated accuracy corpus.
Verify Windows spawning, Linux/macOS CI and the actual Docker worker/volume setup.
Container read-only defaults need an explicit writable job volume only when enabled.

Update API_References, SDK_References, README, CHANGELOG and runnable inline
examples together. Explain sync vs awaited async vs durable jobs, storage/expiry,
retry/idempotency, cancellation, partial outcomes and single-host limitations.
Local ignored examples must not become required by CI or published docs.

Release gate: all six areas have executable evidence, current direct parsing
remains compatible, and documentation matches implemented behavior. Publication
and deployment are separate actions requiring user authorization.

# API Reference

The reference below is generated from the FastAPI OpenAPI schema exported from `backend/main.py` at documentation build time.

!!! note "Production environments"
    Live Swagger UI (`/docs`), ReDoc (`/redoc`), and `/openapi.json` are **disabled** when `APP_ENV=production`. Use this static reference or export the schema from a non-production environment.

## Authentication

| Environment | Auth behavior |
|-------------|---------------|
| `APP_ENV=development` or `test` | Auth bypass — no API key required |
| `APP_ENV=production` | Bearer token required on protected routes |

Protected routes expect:

```http
Authorization: Bearer cv_live_<prefix>.<secret>
```

Create keys with `python -m backend.cli create-tenant-key`. See [Getting Started](getting-started.md#generate-an-api-key).

## Major routes

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/upload` | Upload a document for background ingestion |
| `GET` | `/documents` | List tenant document summaries |
| `GET` | `/documents/{document_id}/status` | Poll ingestion status |
| `GET` | `/documents/{document_id}/status/stream` | SSE ingestion progress |
| `DELETE` | `/documents/{document_id}` | Delete a document |
| `POST` | `/chat` | RAG chat (non-streaming) |
| `POST` | `/chat/stream` | RAG chat (SSE streaming) |
| `POST` | `/chat/batch` | Batch compare or synthesize |
| `POST` | `/sessions` | Create a chat session |
| `GET` | `/sessions` | List sessions |
| `GET` | `/sessions/{session_id}` | Get session metadata |
| `GET` | `/sessions/{session_id}/history` | Session message history |
| `DELETE` | `/sessions/{session_id}` | Delete a session |
| `GET` | `/status` | Service health and configuration summary |
| `GET` | `/queue/stats` | Ingestion queue statistics |

## Document metadata

`POST /upload` accepts an optional multipart form field `metadata`: a JSON object of flat key/value pairs, for example `{"department": "legal", "year": 2026, "final": true}`. Values must be strings, numbers, or booleans. Nested objects, arrays, `null` values, non-object JSON (including a top-level array), and invalid JSON are rejected with `422`. Omitting `metadata` stores `{}`.

`GET /documents` and `GET /documents/{document_id}/status` return the stored object as `metadata`. Size is bounded by `DOCUMENT_METADATA_MAX_KEYS` (default 20) and `DOCUMENT_METADATA_MAX_BYTES` (default 4096). Retrieval can be narrowed by metadata with `metadata_filter` (see [Retrieval overrides](#retrieval-overrides)).

## Replacing a document

`POST /upload` accepts an optional multipart form field `replace_document_id` (a UUID). Instead of creating a new document, the upload re-ingests the file into that existing document, which keeps its `document_id`. Chat sessions bound to the document keep working after the swap. Omitting the field creates a new `document_id` as before.

Replacement is staged:

- While the new file is being processed, the document stays `completed` and its **prior content remains retrievable**. `GET /documents/{document_id}/status`, the status stream, and `GET /documents` report `status_detail: "replacement_in_progress"` (the upload response includes it too). Retrieval and chat never see a mix of old and new chunks from the cutover itself.
- On success the old chunks are swapped for the new ones in one transaction. `file_name` and `metadata` are updated to the new upload's values, and `status_detail` is cleared. Clients should wait for `status_detail` to clear, not for `status == "completed"`, which is already true.
- On failure the prior content is left untouched, `status` is not changed to `failed`, and the document reports `status_detail: "replacement_failed"` with an `error` object. A later replacement can be attempted.

Eligible documents are tenant-owned documents in `completed` or `failed` state with no replacement already running.

| Status | `code` | Meaning |
|--------|--------|---------|
| `404` | `document_not_found` | No such document for this tenant |
| `409` | `replacement_in_progress` | A replacement is already running for this document |
| `409` | `document_processing` | The document is still in its initial ingestion |
| `422` | `invalid_replace_document_id` | `replace_document_id` is not a valid UUID |

`DELETE /documents/{document_id}` returns `409` with `document_replacing` while a replacement is running.

Not included: version history, `document_key` upsert, bulk replace, and content-hash dedup. Hybrid retrieval issues two statements per request, so a single request that straddles the exact commit instant of a cutover could in principle mix old and new chunks. Each statement and the cutover itself are atomic.

Existing databases need migration `backend/db/init/014_document_replacement.sql` (adds `documents.status_detail`) applied with `psql -f`.

## Retrieval overrides

`POST /chat`, `POST /chat/stream`, and `POST /chat/batch` accept optional retrieval overrides. Set them as top-level fields or inside a `retrieval` object. For each field, an explicit top-level value wins over the `retrieval` object, which wins over the process environment. Omitting them leaves Phase 3 behavior: the effective config is the process default.

| Field | Meaning |
|-------|---------|
| `match_count` | Chunks to retrieve. May stay top-level; it merges into the effective config. |
| `scope` | `session` or `tenant`. Same merge rule as `match_count`. |
| `hybrid_enabled` | Override `HYBRID_RETRIEVAL_ENABLED` for this request. |
| `reranking_enabled` | Override `ENABLE_RERANKING` for this request. |
| `reranker_provider` | Override `RERANKER_PROVIDER` when reranking runs. v1 accepts `similarity`. |
| `query_transformation_enabled` | Explicitly enable or disable query transformation. |
| `query_transformation_strategy` | `rewrite`, `expand`, `stepback`, or `none`. |
| `skip_history_transform` | When `false` on a compare-style batch item, session history is passed into query transformation. |
| `metadata_filter` | Narrow which in-scope documents participate in retrieval by their stored document metadata. See below. |

A strategy other than `none` enables transformation for that request even when `QUERY_TRANSFORMATION_ENABLED=false`. `query_transformation_enabled=false` disables it even if a strategy is set. `query_transformation_enabled=true` with strategy `none` does not run rewrite, expand, or step-back. Those strategies are never turned on by omission alone.

### Metadata filter

`metadata_filter` is an object whose keys are document metadata field names. Values are strings, numbers, or booleans, matching what `POST /upload` stores. Comparison is same-type, and strings are case-sensitive.

- Exact match: `{ "department": "legal" }`. The document must have the key and an equal value.
- Scalar in-list: `{ "department": { "in": ["legal", "hr"] } }`. The document value must equal one listed value. The list must be non-empty.
- Multiple keys are ANDed.

Nested objects, arrays as operands, `null`, an empty object, unknown operators, and any other shape are rejected with `422`. The filter is bounded by `DOCUMENT_METADATA_MAX_KEYS` and `DOCUMENT_METADATA_MAX_BYTES`. Array metadata such as tags is not supported in v1.

The filter applies at document level, after the session or tenant scope is resolved and always within the authenticated tenant. `doc_id` stays required on `POST /chat` and `POST /chat/stream`. The anchor document must itself match the filter. Otherwise the request returns an error with code `anchor_document_excluded_by_filter` (an `error` event on the stream). On `POST /chat/batch`, documents that do not match are dropped from each item's scope, and an item left with no documents reports `no_documents_in_scope`. Omitting `metadata_filter` leaves retrieval unchanged.

## History overrides

The same three endpoints accept an optional `history` object. It does not enable rewrite, expand, or step-back. Omitting it keeps the process defaults.

| Field | Meaning |
|-------|---------|
| `retrieval_history_window` | Recent messages used to decide and run standalone resolution. Integer 0–100. Default `QUERY_TRANSFORMATION_HISTORY_WINDOW` (6). |
| `generation_history_window` | Recent messages injected into the answer context. Integer 0–100. Default `GENERATION_HISTORY_WINDOW` (10). |
| `history_aware_retrieval` | When `true`, run the need-resolution gate. When explicitly `false`, never resolve history for that request. Default `HISTORY_AWARE_RETRIEVAL_DEFAULT` (true). |
| `max_generation_history_chars` | Optional cap on generation-history characters, applied before `MAX_CONTEXT_CHARS` drops chunks. |

An extra resolution LLM call happens only when `history_aware_retrieval` is effectively true, the retrieval window is non-empty, transform history is not skipped, and the gate decides the question depends on prior turns. The gate skips the call when the window is empty, the question is already a full standalone query (no unresolved pronouns or elliptical follow-ups such as "what about …" or "and the …"), or an explicit topic-switch cue is present without those references. Cues include "new topic", "switching topics", "change of subject", "different question", "unrelated question", "forget the previous", "ignore the previous", and "starting over". Ambiguous questions, including a topic cue that still contains a pronoun, still resolve.

`HISTORY_AWARE_RETRIEVAL_DEFAULT=false` with the field omitted restores Phase 3: resolution runs only when `QUERY_TRANSFORMATION_ENABLED=true`, and then for every non-empty window, without the gate. A failed resolution call falls back to the original question and does not fail the chat.

Compare-style batch items (one `doc_id`) still skip generation history and turn persistence. They also skip transform history unless `skip_history_transform` is explicitly false. A normal multi-document batch item with a session uses the history settings above.

### Effective retrieval query

After history resolution and transformation, one primary retrieval query is the first string in the final query list.

- Vector search embeds that primary query. `expand` and `stepback` still embed each query in the list and merge candidates the way Phase 3 does.
- Hybrid keyword / full-text search uses the primary query, not the raw user question, when transformation or history resolution changed the text.
- Hybrid fusion uses this request's `hybrid_enabled` value.
- Reranking scores candidates against the primary query.

`RETRIEVAL_MAX_CONCURRENCY`, embedding model selection, provider API keys, and queue settings stay process-level. `MAX_CONTEXT_CHARS` is not request-configurable; debug and eval snapshots still record the resolved process value.

There is no public HTTP retrieval endpoint. Chat still generates the answer. Eval and other internal callers use `retrieve_for_documents`.

## Interactive schema

<swagger-ui src="assets/openapi.json"/>

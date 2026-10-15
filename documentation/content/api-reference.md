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

`metadata_filter` is reserved for Metadata-First Document Ingestion and Retrieval Filters and is not accepted in v1.

A strategy other than `none` enables transformation for that request even when `QUERY_TRANSFORMATION_ENABLED=false`. `query_transformation_enabled=false` disables it even if a strategy is set. `query_transformation_enabled=true` with strategy `none` does not run rewrite, expand, or step-back. Those strategies are never turned on by omission alone.

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

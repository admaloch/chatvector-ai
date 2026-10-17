# RFC: Knowledge Graph and GraphRAG Retrieval (Experimental)

Status: **experimental spike** (default off)  
Issue: [#515](https://github.com/chatvector-ai/chatvector-ai/issues/515)  
Flag: `ENABLE_GRAPH_RETRIEVAL=false` (must remain the default)

This RFC bounds GraphRAG research so it does not become a default dependency
or a parallel production retrieval stack. pgvector remains the default path.

## 1. Approach

Graph retrieval is an **optional overlay** on the existing pipeline, not a
replacement:

```
query → transform → embed → pgvector (+ optional hybrid FTS/RRF)
      → optional graph expansion (this RFC, flag-gated)
      → optional rerank → context assembly → LLM
```

When `ENABLE_GRAPH_RETRIEVAL` is false (default):

- Chat, batch, stream, and eval use today's pgvector / hybrid / rerank path.
- The graph module is not imported on that path.
- No graph database client is required at install or runtime.

When the flag is true, a **fixture-level prototype** may expand vector seed
chunks by one hop over an in-memory entity–chunk graph loaded from JSON.
Expansion is a no-op if no fixture is configured. This is enough to measure
whether graph structure recovers multi-hop evidence that cosine search
misses; it is not a production knowledge graph.

### What "GraphRAG" means here

Two common readings are out of scope for this spike:

- **Community-summary GraphRAG** (Microsoft GraphRAG-style): LLM entity
  extraction, community detection, hierarchical summaries. High cost, new
  ingestion pipeline, not a fixture prototype.
- **Agent-orchestrated graph tools**: LangGraph/agent frameworks walking a
  graph UI. Explicit non-goal of #515.

The in-scope reading is **entity–chunk expansion**: vector search finds seed
chunks; shared entities and typed relations pull in neighboring chunks; the
existing reranker (if enabled) reorders the enlarged candidate set.

## 2. Data model options

### Option A — In-memory fixture graph (prototype; implemented)

JSON file of entities, mention edges (entity → chunk), and relations
(entity → entity). Loaded via `GRAPH_RETRIEVAL_FIXTURE_PATH`. Neighbor
chunk payloads live in the fixture so expansion does not need extra DB
round-trips.

- **Pros:** Zero new dependencies; deterministic tests; tenant-safe (fixture
  is process-local, not a shared store); matches "fixture-level prototype."
- **Cons:** Not durable; not built from real documents; cannot serve
  production corpora.

**Recommendation for this spike:** Option A only.

### Option B — PostgreSQL adjacency tables

New tables, for example `graph_entities`, `graph_mentions`,
`graph_relations`, keyed by `tenant_id` + `document_id`, populated during
ingestion. Retrieval joins mention rows for seed chunks, walks one hop, and
fetches neighbor `document_chunks` with the existing tenant filters.

- **Pros:** Stays on PostgreSQL (no graph DB); tenant isolation reuses
  current ACL; migrations fit `backend/db/init/`.
- **Cons:** Schema + extraction pipeline; hop queries can N+1 without
  careful SQL; still need an entity extractor.

**When to consider:** After Option A shows recall lift on a real eval set
and go criteria in §5 are met.

### Option C — Optional graph database (Neo4j / Neptune / etc.)

Ship as an extra/optional requirements file only. Default install must not
pull a graph driver.

- **Pros:** Native multi-hop queries.
- **Cons:** New operational dependency; dual-write ingestion; tenant
  isolation must be re-proved; conflicts with "PostgreSQL/pgvector is the
  database" architecture.

**Recommendation:** No-go for default. Revisit only if Option B cannot
express the hop patterns a domain actually needs.

### Extraction (all durable options)

Any path beyond fixtures needs a way to emit entities and relations:

| Method | Fit |
| --- | --- |
| Human/fixture annotations | Spike only (this RFC) |
| Rule / glossary match | Cheap, domain-specific, low recall |
| LLM extraction at ingest | Quality vs cost/latency; must be tenant-scoped and retry-safe |
| Off-the-shelf NER | Extra NLP dep; weak on domain terms |

Do not add an extractor to the default ingest worker in this spike.

## 3. Eval methodology

Reuse the existing retrieval eval tool (`python -m backend.cli eval`,
`services/eval_service.py`). Do not invent a second harness.

**Metrics (already implemented):** recall@k, MRR, nDCG@k with k ∈ {5, 10}.
Ground truth is `relevant_chunks` (`[document_id, chunk_index]`) and/or
`relevant_doc_ids`.

**Arms to compare** (same dataset, sequential arms — eval already forbids
mutating process config per arm, so graph on/off is a process-env contrast
or a dedicated fixture test, not a request-scoped retrieval field):

| Arm | Config |
| --- | --- |
| Baseline vector | `ENABLE_GRAPH_RETRIEVAL=false` (default) |
| Hybrid | `HYBRID_RETRIEVAL_ENABLED=true`, graph off |
| Graph overlay | `ENABLE_GRAPH_RETRIEVAL=true` + fixture path |
| Graph + hybrid | both on |

**Datasets:**

1. **Sanity:** `backend/tests/fixtures/eval/basic_queries.json` — graph
   expansion must not crash and should not tank single-hop recall. If the
   fixture graph does not mention those chunks, expansion is a no-op.
2. **Multi-hop (spike):** `backend/tests/fixtures/graph/multihop.json` —
   seed chunk does not contain the answer; a 1-hop relation does. Vector
   recall@k of the answer chunk is 0; graph expansion should recover it.

**What this eval does not measure:** answer faithfulness, extraction
quality on live documents, or latency at corpus scale. Those are go
criteria for a follow-up, not this spike.

Eval/benchmark runs must not enable the graph flag in their own process
unless they are specifically measuring this overlay — same coordination
rule as `ENABLE_EMBEDDING_CACHE`.

## 4. Risks

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Default-path regression | Chat quality and tests must stay byte-stable when the flag is off | Gate on `ENABLE_GRAPH_RETRIEVAL`; lazy-import the module; no required deps |
| Parallel retrieval stack | A second production path diverges from pgvector | Overlay only; no new score_type or request field in this spike |
| Graph DB in default install | Ops and license burden | Forbidden; optional extra only if a later issue chooses Option C |
| Tenant leakage | Neighbor hops could pull another tenant's chunks | Fixture graph is process-local; any durable model must filter by `tenant_id` on every hop |
| Extraction cost/noise | Bad entities amplify irrelevant neighbors | Fixture-only until eval on real docs beats baseline without recall regression |
| Latency | Extra hops before rerank | 1 hop, cap extra candidates, skip entirely when flag is off |
| Public API creep | New `score_type` / request overrides lock clients in | Prototype does not add citation fields or per-request graph toggles |

## 5. Go / no-go criteria

### Production default (`ENABLE_GRAPH_RETRIEVAL=true` as shipped default)

**No-go** unless all of the following hold in a follow-up issue:

1. On a labeled corpus larger than the spike fixture, graph overlay
   improves recall@10 **and** nDCG@10 versus vector and versus hybrid,
   without lowering MRR on the basic single-hop set.
2. p95 retrieval latency with the flag off is unchanged; with the flag on,
   expansion adds a documented, acceptable bound.
3. No new *required* runtime dependency.
4. Tenant isolation tests cover hop results.
5. Entity extraction is specified, costed, and not a blocking ingest
   failure mode.

Phase 4 completion does **not** require GraphRAG. A negative or mixed
eval is an acceptable outcome of this issue.

### Spike success (this issue)

**Go for keeping the prototype behind the flag** if:

- Default tests pass with the flag off and `requirements.txt` unchanged.
- The fixture prototype recovers at least one multi-hop relevant chunk
  that the seed set did not contain.
- This RFC and the eval note exist in-repo.

**No-go for enabling the flag in production Compose / `.env.example`
defaults** regardless of fixture results.

## 6. Prototype contract

Implemented in `backend/services/graph_retrieval.py`:

- `ENABLE_GRAPH_RETRIEVAL` (default false) and optional
  `GRAPH_RETRIEVAL_FIXTURE_PATH`.
- Expansion runs after vector/hybrid search and before rerank, only when
  the flag is true.
- Neighbor chunks come from the fixture payload (no graph DB, no extra
  required packages).
- Missing or empty fixture → no-op (seed ranking unchanged).
- No request-body override; no new `score_type`.

## 7. Non-goals (from #515)

- Replace pgvector as default.
- Production GraphRAG as a Phase 4 completion requirement.
- Agent frameworks or graph UI products.

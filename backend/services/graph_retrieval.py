"""Experimental fixture-level GraphRAG overlay.

Gated by ``ENABLE_GRAPH_RETRIEVAL`` (default false). No graph database and no
extra packages: the graph is an in-memory JSON fixture. See
``backend/rfcs/graph-retrieval.md``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.config import ROOT_DIR, config
from db.base import ChunkMatch

logger = logging.getLogger(__name__)

DEFAULT_EXTRA_CANDIDATES = 5

ChunkKey = tuple[str, int]


@dataclass(frozen=True)
class GraphChunk:
    document_id: str
    chunk_index: int
    chunk_id: str
    chunk_text: str
    file_name: str | None = None


@dataclass
class EntityChunkGraph:
    mentions_by_chunk: dict[ChunkKey, set[str]] = field(default_factory=dict)
    chunks_by_entity: dict[str, set[ChunkKey]] = field(default_factory=dict)
    related_entities: dict[str, set[str]] = field(default_factory=dict)
    chunk_payloads: dict[ChunkKey, GraphChunk] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.chunk_payloads


def _chunk_key(document_id: str | None, chunk_index: int | None) -> ChunkKey | None:
    if document_id is None or chunk_index is None:
        return None
    return (document_id, chunk_index)


def _resolve_fixture_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_file():
        return path
    candidate = ROOT_DIR / raw
    return candidate


def load_fixture_graph(path: str | Path) -> EntityChunkGraph:
    """Load an entity–chunk graph from JSON. Raises on missing file or bad JSON."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    graph = EntityChunkGraph()

    for row in raw.get("chunks") or []:
        document_id = str(row["document_id"])
        chunk_index = int(row["chunk_index"])
        key = (document_id, chunk_index)
        graph.chunk_payloads[key] = GraphChunk(
            document_id=document_id,
            chunk_index=chunk_index,
            chunk_id=str(row.get("id") or f"{document_id}:{chunk_index}"),
            chunk_text=str(row.get("chunk_text") or ""),
            file_name=row.get("file_name"),
        )

    for row in raw.get("mentions") or []:
        entity_id = str(row["entity_id"])
        key = (str(row["document_id"]), int(row["chunk_index"]))
        graph.mentions_by_chunk.setdefault(key, set()).add(entity_id)
        graph.chunks_by_entity.setdefault(entity_id, set()).add(key)

    for row in raw.get("relations") or []:
        source = str(row["source"])
        target = str(row["target"])
        graph.related_entities.setdefault(source, set()).add(target)
        graph.related_entities.setdefault(target, set()).add(source)

    return graph


def _neighbor_keys(seeds: list[ChunkMatch], graph: EntityChunkGraph) -> list[ChunkKey]:
    """1-hop neighbor chunk keys: co-mentions plus related-entity mentions."""
    seed_keys: set[ChunkKey] = set()
    seed_entities: set[str] = set()
    for chunk in seeds:
        key = _chunk_key(chunk.document_id, chunk.chunk_index)
        if key is None:
            continue
        seed_keys.add(key)
        seed_entities.update(graph.mentions_by_chunk.get(key, set()))

    neighbor_entities = set(seed_entities)
    for entity_id in seed_entities:
        neighbor_entities.update(graph.related_entities.get(entity_id, set()))

    ordered: list[ChunkKey] = []
    seen: set[ChunkKey] = set(seed_keys)
    for entity_id in sorted(neighbor_entities):
        for key in sorted(graph.chunks_by_entity.get(entity_id, set())):
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered


def _chunk_match_from_payload(payload: GraphChunk) -> ChunkMatch:
    return ChunkMatch(
        id=payload.chunk_id,
        chunk_text=payload.chunk_text,
        document_id=payload.document_id,
        chunk_index=payload.chunk_index,
        similarity=0.0,
        file_name=payload.file_name,
    )


def expand_chunks(
    seeds: list[ChunkMatch],
    graph: EntityChunkGraph,
    *,
    extra_limit: int = DEFAULT_EXTRA_CANDIDATES,
) -> list[ChunkMatch]:
    """Append 1-hop neighbor chunks from ``graph`` that are not already seeds."""
    if not seeds or graph.is_empty() or extra_limit <= 0:
        return list(seeds)

    extras: list[ChunkMatch] = []
    for key in _neighbor_keys(seeds, graph):
        payload = graph.chunk_payloads.get(key)
        if payload is None:
            continue
        extras.append(_chunk_match_from_payload(payload))
        if len(extras) >= extra_limit:
            break
    return list(seeds) + extras


def _load_configured_graph() -> EntityChunkGraph | None:
    raw_path = config.GRAPH_RETRIEVAL_FIXTURE_PATH
    if not raw_path or not str(raw_path).strip():
        return None
    path = _resolve_fixture_path(str(raw_path).strip())
    if not path.is_file():
        logger.warning("GRAPH_RETRIEVAL_FIXTURE_PATH=%s is not a file; skipping graph expansion", path)
        return None
    try:
        return load_fixture_graph(path)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Failed to load graph fixture %s: %s; skipping graph expansion", path, exc)
        return None


def expand_chunks_if_enabled(
    seeds: list[ChunkMatch],
    *,
    extra_limit: int = DEFAULT_EXTRA_CANDIDATES,
) -> list[ChunkMatch]:
    """No-op unless ``ENABLE_GRAPH_RETRIEVAL`` is true and a fixture loads."""
    if not config.ENABLE_GRAPH_RETRIEVAL:
        return list(seeds)
    graph = _load_configured_graph()
    if graph is None:
        return list(seeds)
    return expand_chunks(seeds, graph, extra_limit=extra_limit)

"""Staged replacement semantics at the database layer (real PostgreSQL).

Assertions are made on retrieval results (``find_similar_chunks``), not on
``document_chunks`` row counts.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid

import pytest

pytest.importorskip("pgvector")

from core.config import STALE_INGESTION_STATUSES, get_embedding_dim
from db.base import ChunkRecord
from db.sqlalchemy_service import (
    REPLACEMENT_FAILED,
    REPLACEMENT_IN_PROGRESS,
    SQLAlchemyService,
)
from services.api_key_service import create_tenant, reset_session_factory

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Psycopg async mode not supported with ProactorEventLoop on Windows",
)

OLD_PHRASE = "OLD-EDITION penguins migrate in spring"
NEW_PHRASE = "NEW-EDITION albatrosses glide over oceans"


@pytest.fixture(autouse=True)
def _reset_api_key_session_factory():
    reset_session_factory()
    yield
    reset_session_factory()


def _unit_vector(position: int) -> list[float]:
    vector = [0.0] * get_embedding_dim()
    vector[position] = 1.0
    return vector


def _records(texts: list[str], embedding: list[float]) -> list[ChunkRecord]:
    cursor = 0
    records = []
    for index, text in enumerate(texts):
        records.append(
            ChunkRecord(
                chunk_text=text,
                embedding=embedding,
                chunk_index=index,
                character_offset_start=cursor,
                character_offset_end=cursor + len(text),
            )
        )
        cursor += len(text)
    return records


async def _make_completed_document(
    svc: SQLAlchemyService,
    tenant_id: str,
    *,
    file_name: str = "v1.pdf",
    metadata: dict | None = None,
) -> str:
    doc_id = await svc.create_document(file_name, tenant_id=tenant_id, metadata=metadata)
    await svc.store_chunks_with_embeddings(
        doc_id,
        _records([OLD_PHRASE, f"{OLD_PHRASE} (part two)"], _unit_vector(0)),
        tenant_id=tenant_id,
    )
    await svc.update_document_status(
        doc_id,
        "completed",
        tenant_id=tenant_id,
        chunks={"total": 2, "processed": 2},
    )
    return doc_id


async def _retrieved_texts(
    svc: SQLAlchemyService, doc_id: str, tenant_id: str
) -> list[str]:
    """Texts returned for queries aimed at the old *and* the new embedding."""
    texts: list[str] = []
    for position in (0, 1):
        matches = await svc.find_similar_chunks(
            doc_id, _unit_vector(position), match_count=20, tenant_id=tenant_id
        )
        texts.extend(match.chunk_text for match in matches)
    return texts


def _fingerprint(texts: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(set(texts))).encode()).hexdigest()


@pytest.fixture
async def tenant_id() -> str:
    tenant = f"replace-{uuid.uuid4().hex[:8]}"
    await create_tenant("Replacement test", tenant_id=tenant)
    return tenant


@pytest.fixture
def svc() -> SQLAlchemyService:
    return SQLAlchemyService()


@pytest.mark.asyncio
async def test_begin_replacement_claims_completed_document_once(svc, tenant_id):
    doc_id = await _make_completed_document(svc, tenant_id)

    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "in_progress"

    payload = await svc.get_document_status(doc_id, tenant_id)
    assert payload["status"] == "completed"
    assert payload["status_detail"] == REPLACEMENT_IN_PROGRESS


@pytest.mark.asyncio
async def test_concurrent_begin_replacement_has_single_winner(svc, tenant_id):
    doc_id = await _make_completed_document(svc, tenant_id)

    outcomes = await asyncio.gather(
        *[svc.begin_document_replacement(doc_id, tenant_id) for _ in range(5)]
    )

    assert sorted(outcomes) == ["in_progress"] * 4 + ["started"]


@pytest.mark.asyncio
async def test_begin_replacement_rejects_in_flight_ingestion(svc, tenant_id):
    doc_id = await svc.create_document("busy.pdf", tenant_id=tenant_id)
    await svc.update_document_status(doc_id, "embedding", tenant_id=tenant_id)

    assert await svc.begin_document_replacement(doc_id, tenant_id) == "not_eligible"
    payload = await svc.get_document_status(doc_id, tenant_id)
    assert payload["status_detail"] is None


@pytest.mark.asyncio
async def test_begin_replacement_is_tenant_scoped(svc, tenant_id):
    doc_id = await _make_completed_document(svc, tenant_id)
    other_tenant = f"replace-{uuid.uuid4().hex[:8]}"
    await create_tenant("Other tenant", tenant_id=other_tenant)

    assert await svc.begin_document_replacement(doc_id, other_tenant) == "not_found"
    assert (
        await svc.begin_document_replacement(str(uuid.uuid4()), tenant_id) == "not_found"
    )
    payload = await svc.get_document_status(doc_id, tenant_id)
    assert payload["status_detail"] is None


@pytest.mark.asyncio
async def test_prior_content_stays_retrievable_while_replacement_in_progress(
    svc, tenant_id
):
    doc_id = await _make_completed_document(svc, tenant_id)
    before = await _retrieved_texts(svc, doc_id, tenant_id)

    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"

    during = await _retrieved_texts(svc, doc_id, tenant_id)
    assert during == before
    assert OLD_PHRASE in during[0]
    assert all(NEW_PHRASE not in text for text in during)


@pytest.mark.asyncio
async def test_failed_replacement_keeps_prior_content_and_completed_status(
    svc, tenant_id
):
    doc_id = await _make_completed_document(svc, tenant_id)
    before = await _retrieved_texts(svc, doc_id, tenant_id)
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"

    error = {"stage": "embedding", "code": "pipeline_error", "message": "boom"}
    await svc.set_document_status_detail(
        doc_id, REPLACEMENT_FAILED, tenant_id, error=error
    )

    payload = await svc.get_document_status(doc_id, tenant_id)
    assert payload["status"] == "completed"
    assert payload["status_detail"] == REPLACEMENT_FAILED
    assert payload["error"] == error
    assert await _retrieved_texts(svc, doc_id, tenant_id) == before

    # A failed replacement does not block the next attempt.
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"
    restarted = await svc.get_document_status(doc_id, tenant_id)
    assert restarted["status_detail"] == REPLACEMENT_IN_PROGRESS
    assert restarted["error"] is None


@pytest.mark.asyncio
async def test_successful_cutover_returns_only_new_content(svc, tenant_id):
    doc_id = await _make_completed_document(svc, tenant_id)
    old_fingerprint = _fingerprint(await _retrieved_texts(svc, doc_id, tenant_id))
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"

    new_texts = [NEW_PHRASE, f"{NEW_PHRASE} (part two)", f"{NEW_PHRASE} (part three)"]
    chunk_ids = await svc.replace_document_content(
        doc_id,
        _records(new_texts, _unit_vector(1)),
        tenant_id,
        file_name="v2.pdf",
    )

    assert len(chunk_ids) == 3
    after = await _retrieved_texts(svc, doc_id, tenant_id)
    assert set(after) == set(new_texts)
    assert all(OLD_PHRASE not in text for text in after)
    assert _fingerprint(after) != old_fingerprint
    assert _fingerprint(after) == _fingerprint(new_texts)

    payload = await svc.get_document_status(doc_id, tenant_id)
    assert payload["status"] == "completed"
    assert payload["status_detail"] is None
    assert payload["error"] is None
    assert payload["chunks"] == {"total": 3, "processed": 3}
    document = await svc.get_document(doc_id, tenant_id)
    assert document["file_name"] == "v2.pdf"


@pytest.mark.asyncio
async def test_cutover_updates_metadata_only_when_provided(svc, tenant_id):
    doc_id = await _make_completed_document(
        svc, tenant_id, metadata={"department": "legal"}
    )

    await svc.replace_document_content(
        doc_id,
        _records([NEW_PHRASE], _unit_vector(1)),
        tenant_id,
        file_name="keep-meta.pdf",
    )
    kept = await svc.get_document_status(doc_id, tenant_id)
    assert kept["metadata"] == {"department": "legal"}

    await svc.replace_document_content(
        doc_id,
        _records([NEW_PHRASE], _unit_vector(1)),
        tenant_id,
        file_name="new-meta.pdf",
        metadata={"department": "finance", "year": 2026},
    )
    updated = await svc.get_document_status(doc_id, tenant_id)
    assert updated["metadata"] == {"department": "finance", "year": 2026}


@pytest.mark.asyncio
async def test_cutover_failure_rolls_back_and_prior_content_survives(svc, tenant_id):
    doc_id = await _make_completed_document(svc, tenant_id)
    before = await _retrieved_texts(svc, doc_id, tenant_id)
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"

    duplicate_index = _records([NEW_PHRASE, f"{NEW_PHRASE} again"], _unit_vector(1))
    duplicate_index[1].chunk_index = duplicate_index[0].chunk_index  # unique violation

    with pytest.raises(Exception):
        await svc.replace_document_content(
            doc_id, duplicate_index, tenant_id, file_name="broken.pdf"
        )

    assert await _retrieved_texts(svc, doc_id, tenant_id) == before
    payload = await svc.get_document_status(doc_id, tenant_id)
    assert payload["status"] == "completed"
    assert payload["chunks"] == {"total": 2, "processed": 2}
    document = await svc.get_document(doc_id, tenant_id)
    assert document["file_name"] == "v1.pdf"


@pytest.mark.asyncio
async def test_cutover_for_unknown_document_raises(svc, tenant_id):
    with pytest.raises(ValueError):
        await svc.replace_document_content(
            str(uuid.uuid4()),
            _records([NEW_PHRASE], _unit_vector(1)),
            tenant_id,
            file_name="ghost.pdf",
        )


@pytest.mark.asyncio
async def test_failed_document_can_be_replaced_and_becomes_retrievable(svc, tenant_id):
    doc_id = await svc.create_document("broken.pdf", tenant_id=tenant_id)
    await svc.update_document_status(
        doc_id,
        "failed",
        tenant_id=tenant_id,
        error={"stage": "extracting", "message": "bad pdf"},
    )
    assert await _retrieved_texts(svc, doc_id, tenant_id) == []

    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"
    during = await svc.get_document_status(doc_id, tenant_id)
    assert during["status"] == "failed"
    assert during["error"] == {"stage": "extracting", "message": "bad pdf"}

    await svc.replace_document_content(
        doc_id, _records([NEW_PHRASE], _unit_vector(1)), tenant_id, file_name="fixed.pdf"
    )

    assert set(await _retrieved_texts(svc, doc_id, tenant_id)) == {NEW_PHRASE}
    after = await svc.get_document_status(doc_id, tenant_id)
    assert after["status"] == "completed"
    assert after["error"] is None


@pytest.mark.asyncio
async def test_failed_replacement_of_failed_document_keeps_prior_error(svc, tenant_id):
    doc_id = await svc.create_document("broken.pdf", tenant_id=tenant_id)
    original = {"stage": "extracting", "code": "no_text_extracted", "message": "bad pdf"}
    await svc.update_document_status(
        doc_id,
        "failed",
        tenant_id=tenant_id,
        error=original,
    )
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"

    await svc.set_document_status_detail(
        doc_id,
        REPLACEMENT_IN_PROGRESS,
        tenant_id=tenant_id,
    )
    during = await svc.get_document_status(doc_id, tenant_id)
    assert during["status"] == "failed"
    assert during["error"] == original

    replacement_error = {
        "stage": "embedding",
        "code": "provider_timeout",
        "message": "timed out",
    }
    await svc.set_document_status_detail(
        doc_id,
        REPLACEMENT_FAILED,
        tenant_id=tenant_id,
        error=replacement_error,
    )
    failed = await svc.get_document_status(doc_id, tenant_id)
    assert failed["status"] == "failed"
    assert failed["status_detail"] == REPLACEMENT_FAILED
    assert failed["error"]["code"] == "provider_timeout"
    assert failed["error"]["prior_error"] == original
    assert await _retrieved_texts(svc, doc_id, tenant_id) == []

    await svc.begin_document_replacement(doc_id, tenant_id)
    await svc.set_document_status_detail(
        doc_id,
        REPLACEMENT_FAILED,
        tenant_id=tenant_id,
        error={"stage": "embedding", "code": "pipeline_error", "message": "again"},
    )
    again = await svc.get_document_status(doc_id, tenant_id)
    assert again["error"]["code"] == "pipeline_error"
    assert again["error"]["prior_error"] == original

    await svc.begin_document_replacement(doc_id, tenant_id)
    await svc.replace_document_content(
        doc_id, _records([NEW_PHRASE], _unit_vector(1)), tenant_id, file_name="fixed.pdf"
    )
    cutover = await svc.get_document_status(doc_id, tenant_id)
    assert cutover["status"] == "completed"
    assert cutover["error"] is None
    assert set(await _retrieved_texts(svc, doc_id, tenant_id)) == {NEW_PHRASE}


@pytest.mark.asyncio
async def test_completed_replacement_failure_keeps_prior_content(svc, tenant_id):
    doc_id = await _make_completed_document(svc, tenant_id)
    before = await _retrieved_texts(svc, doc_id, tenant_id)
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"
    await svc.set_document_status_detail(
        doc_id,
        REPLACEMENT_FAILED,
        tenant_id=tenant_id,
        error={"stage": "embedding", "code": "pipeline_error", "message": "nope"},
    )
    payload = await svc.get_document_status(doc_id, tenant_id)
    assert payload["status"] == "completed"
    assert payload["error"]["code"] == "pipeline_error"
    assert "prior_error" not in payload["error"]
    assert await _retrieved_texts(svc, doc_id, tenant_id) == before


@pytest.mark.asyncio
async def test_summaries_expose_status_detail_only_when_set(svc, tenant_id):
    doc_id = await _make_completed_document(svc, tenant_id)

    idle = {d["document_id"]: d for d in await svc.list_tenant_document_summaries(tenant_id)}
    assert "status_detail" not in idle[doc_id]

    await svc.begin_document_replacement(doc_id, tenant_id)
    busy = {d["document_id"]: d for d in await svc.list_tenant_document_summaries(tenant_id)}
    assert busy[doc_id]["status"] == "completed"
    assert busy[doc_id]["status_detail"] == REPLACEMENT_IN_PROGRESS


@pytest.mark.asyncio
async def test_startup_reconciliation_resets_replacement_without_touching_content(
    svc, tenant_id
):
    doc_id = await _make_completed_document(svc, tenant_id)
    before = await _retrieved_texts(svc, doc_id, tenant_id)
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"

    reset_ids = await svc.fail_stale_documents_global(STALE_INGESTION_STATUSES)

    assert doc_id in reset_ids
    payload = await svc.get_document_status(doc_id, tenant_id)
    assert payload["status"] == "completed"
    assert payload["status_detail"] == REPLACEMENT_FAILED
    assert payload["error"]["stage"] == "server_restart"
    assert await _retrieved_texts(svc, doc_id, tenant_id) == before
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"


@pytest.mark.asyncio
async def test_startup_reset_of_failed_document_keeps_prior_error(svc, tenant_id):
    doc_id = await svc.create_document("broken.pdf", tenant_id=tenant_id)
    original = {"stage": "extracting", "code": "no_text_extracted", "message": "bad pdf"}
    await svc.update_document_status(
        doc_id, "failed", tenant_id=tenant_id, error=original
    )
    assert await svc.begin_document_replacement(doc_id, tenant_id) == "started"

    await svc.fail_stale_documents_global(STALE_INGESTION_STATUSES)

    payload = await svc.get_document_status(doc_id, tenant_id)
    assert payload["status"] == "failed"
    assert payload["status_detail"] == REPLACEMENT_FAILED
    assert payload["error"]["code"] == "server_restart"
    assert payload["error"]["prior_error"] == original

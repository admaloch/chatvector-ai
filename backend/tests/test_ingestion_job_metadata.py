"""Unit tests for ingestion job metadata helpers."""

from datetime import datetime, timedelta, timezone

from services.ingestion_job_metadata import (
    FALLBACK_FAILURE_CLASS,
    build_public_job,
    compute_durations_ms,
    failure_class_from_error,
    finalize_stored_job_metadata,
    patch_enqueued,
    patch_terminal_failure,
)


def test_failure_class_from_error_uses_code_or_fallback():
    assert failure_class_from_error({"code": "provider_timeout"}) == "provider_timeout"
    assert failure_class_from_error({"message": "x"}) == FALLBACK_FAILURE_CLASS
    assert failure_class_from_error(None) == FALLBACK_FAILURE_CLASS


def test_compute_durations_ms_prefers_enqueued_to_completed():
    enqueued = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    completed = enqueued + timedelta(milliseconds=1500)
    stored = {
        "enqueued_at": enqueued.isoformat().replace("+00:00", "Z"),
        "completed_at": completed.isoformat().replace("+00:00", "Z"),
    }
    assert compute_durations_ms(stored) == {"total": 1500}


def test_finalize_stored_job_metadata_adds_durations():
    enqueued = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    completed = enqueued + timedelta(seconds=1)
    merged = finalize_stored_job_metadata(
        {
            **patch_enqueued(enqueued_at=enqueued, attempt=2),
            **patch_terminal_failure({"code": "pipeline_error"}),
            "completed_at": completed.isoformat().replace("+00:00", "Z"),
        }
    )
    assert merged["attempt"] == 2
    assert merged["durations_ms"]["total"] == 1000


def test_build_public_job_includes_failure_class_for_failed_status():
    job = build_public_job(
        {"enqueued_at": "2026-01-01T12:00:00Z", "failure_class": "embedding_rate_limited"},
        status="failed",
        status_detail=None,
        error={"code": "pipeline_error", "message": "x"},
    )
    assert job is not None
    assert job["failure_class"] == "embedding_rate_limited"


def test_build_public_job_returns_none_for_empty_legacy_row():
    assert build_public_job({}, status="completed", status_detail=None, error=None) is None

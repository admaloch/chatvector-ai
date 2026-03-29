"""Tests for /status health checks and payload helpers."""

from unittest.mock import AsyncMock, patch

import pytest

from routes.status import (
    _embedding_health_check,
    _llm_health_check,
    _overall_status,
)


@pytest.mark.asyncio
async def test_embedding_health_check_ok_when_embedding_succeeds():
    with patch(
        "services.embedding_service.get_embedding",
        new=AsyncMock(return_value=[0.1, 0.2]),
    ):
        result = await _embedding_health_check()

    assert result["status"] == "ok"
    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_embedding_health_check_error_when_embedding_raises():
    with patch(
        "services.embedding_service.get_embedding",
        new=AsyncMock(side_effect=RuntimeError("embedding failed")),
    ):
        result = await _embedding_health_check()

    assert result["status"] == "error"
    assert result["error"] == "embedding failed"


@pytest.mark.asyncio
async def test_llm_health_check_ok_when_generate_answer_succeeds():
    with patch(
        "services.answer_service.generate_answer",
        new=AsyncMock(return_value="All systems nominal."),
    ):
        result = await _llm_health_check()

    assert result["status"] == "ok"
    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_llm_health_check_error_when_generate_answer_raises():
    with patch(
        "services.answer_service.generate_answer",
        new=AsyncMock(side_effect=RuntimeError("LLM unavailable")),
    ):
        result = await _llm_health_check()

    assert result["status"] == "error"
    assert result["error"] == "error"


@pytest.mark.parametrize(
    "db_ok, embedding_ok, llm_ok, expected",
    [
        (True, True, True, "healthy"),
        (True, False, True, "degraded"),
        (True, True, False, "degraded"),
        (True, False, False, "degraded"),
        (False, True, True, "unhealthy"),
        (False, False, False, "unhealthy"),
        (False, True, False, "unhealthy"),
    ],
)
def test_overall_status_combinations(db_ok, embedding_ok, llm_ok, expected):
    assert _overall_status(db_ok, embedding_ok, llm_ok) == expected

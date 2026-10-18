"""Gated history resolution for chat, stream, batch, and eval."""

import json
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.auth import AuthContext
from core.config import config
from services.eval_config import EffectiveRetrievalConfig
from services.eval_fixtures import EvalDataset, EvalQuery, load_dataset
from services.eval_service import run_eval
from services.history_config import HistoryResolutionPolicy
from services.chat_service import (
    answer_question_for_document,
    answer_question_stream_for_document,
    answer_questions_for_documents_batch,
)
from services.query_service import (
    QueryTransformResult,
    StandaloneResolutionCall,
    question_needs_history_resolution,
    transform_query,
)

TEST_AUTH = AuthContext(tenant_id="dev")


def _patch_process_flags(monkeypatch, **flags: object) -> None:
    """Set process flags on every Settings instance this suite can observe.

    Earlier tests call ``importlib.reload(core.config)``, which replaces
    ``core.config.config``. Module-level ``from core.config import config``
    bindings keep the previous instance, and ``HistoryProcessDefaults.from_process``
    imports the live one.
    """
    import core.config as config_module
    import services.chat_service as chat_service_mod
    import services.query_service as query_service_mod

    targets = (
        config,
        config_module.config,
        chat_service_mod.config,
        query_service_mod.config,
    )
    seen: set[int] = set()
    for target in targets:
        if id(target) in seen:
            continue
        seen.add(id(target))
        for name, value in flags.items():
            monkeypatch.setattr(target, name, value)
_HISTORY = [
    {"role": "user", "content": "Summarize the liability section of the agreement."},
    {"role": "assistant", "content": "The liability section limits damages."},
]
_PRONOUN = "What does it say about liability?"
_WHAT_ABOUT = "What about the second clause?"
_STANDALONE = "What is the liability clause in section 4 of the master agreement?"
_TOPIC_SWITCH = "New topic: What is the refund policy for cancelled annual plans?"
_RESOLVED = "What does the agreement say about liability?"


def test_gate_runs_for_pronouns_and_elliptical_followups():
    assert question_needs_history_resolution(_PRONOUN, _HISTORY) == (True, None)
    assert question_needs_history_resolution(_WHAT_ABOUT, _HISTORY) == (True, None)
    assert question_needs_history_resolution("How much was that?", _HISTORY) == (True, None)
    assert question_needs_history_resolution("And the termination clause?", _HISTORY) == (True, None)


def test_gate_runs_for_demonstrative_followups():
    assert question_needs_history_resolution("What does that clause require?", _HISTORY) == (
        True,
        None,
    )
    assert question_needs_history_resolution("How does this section work?", _HISTORY) == (
        True,
        None,
    )
    assert question_needs_history_resolution("Can you explain those terms?", _HISTORY) == (
        True,
        None,
    )


def test_gate_skips_standalone_topic_switch_and_empty_history():
    assert question_needs_history_resolution(_STANDALONE, _HISTORY) == (
        False,
        "standalone_question",
    )
    assert question_needs_history_resolution(
        "How does the arbitration clause work under California law?",
        _HISTORY,
    ) == (False, "standalone_question")
    assert question_needs_history_resolution(_TOPIC_SWITCH, _HISTORY) == (False, "topic_switch")
    assert question_needs_history_resolution(_PRONOUN, []) == (False, "empty_history")
    # A topic cue does not override an unresolved pronoun.
    mixed = "New topic: what does it say about fees?"
    assert question_needs_history_resolution(mixed, _HISTORY)[0] is True


@pytest.mark.asyncio
async def test_history_preview_is_truncated(monkeypatch):
    long_history = [{"role": "user", "content": "x" * 400}]

    async def fake_llm(system_instruction: str, user_text: str) -> str:
        return "resolved follow up"

    monkeypatch.setattr("services.query_service._llm_transform", fake_llm)
    result = await transform_query(
        _PRONOUN,
        history=long_history,
        resolution_policy=HistoryResolutionPolicy(run=True, use_gate=True),
    )
    preview = result.to_retrieval_debug()["history_considered"][0]["content_preview"]
    assert len(preview) == 160
    assert result.to_retrieval_debug()["history_truncation"]["preview_truncated"] is True


@pytest.mark.asyncio
async def test_phase4_policy_resolves_followup_without_enabling_strategy(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_STRATEGY", "rewrite")

    async def fake_llm(system_instruction: str, user_text: str) -> str:
        return _RESOLVED

    async def rewrite_must_not_run(question: str) -> str:
        raise AssertionError("rewrite must not run from history defaults")

    monkeypatch.setattr("services.query_service._llm_transform", fake_llm)
    monkeypatch.setattr("services.query_service.rewrite_query", rewrite_must_not_run)

    result = await transform_query(
        _PRONOUN,
        history=_HISTORY,
        resolution_policy=HistoryResolutionPolicy(run=True, use_gate=True),
    )

    assert result.queries == [_RESOLVED]
    assert result.history_resolved_query == _RESOLVED
    assert result.transformation_strategy is None
    debug = result.to_retrieval_debug()
    assert debug["history_resolution_applied"] is True
    assert debug["history_resolution_latency_ms"] >= 0
    assert "history_resolution_skipped_reason" not in debug
    assert len(debug["history_considered"]) == len(_HISTORY)


@pytest.mark.asyncio
async def test_phase4_policy_skips_llm_for_standalone_question(monkeypatch):
    async def resolve_must_not_run(question: str, history: list[dict]) -> StandaloneResolutionCall:
        raise AssertionError("standalone question must not call the resolution LLM")

    monkeypatch.setattr("services.query_service._call_standalone_resolution", resolve_must_not_run)

    result = await transform_query(
        _STANDALONE,
        history=_HISTORY,
        resolution_policy=HistoryResolutionPolicy(run=True, use_gate=True),
    )

    assert result.queries == [_STANDALONE]
    debug = result.to_retrieval_debug()
    assert debug["history_resolution_applied"] is False
    assert debug["history_resolution_skipped_reason"] == "standalone_question"
    assert "history_resolution_latency_ms" not in debug


@pytest.mark.asyncio
async def test_resolution_failure_falls_back_and_records_debug(monkeypatch):
    async def fake_llm(system_instruction: str, user_text: str) -> None:
        return None

    monkeypatch.setattr("services.query_service._llm_transform", fake_llm)

    result = await transform_query(
        _PRONOUN,
        history=_HISTORY,
        resolution_policy=HistoryResolutionPolicy(run=True, use_gate=True),
    )

    assert result.queries == [_PRONOUN]
    debug = result.to_retrieval_debug()
    assert debug["history_resolution_applied"] is False
    assert debug["history_resolution_fallback_reason"] == "llm_failure"
    assert "history_resolved_query" not in debug
    assert debug["history_resolution_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_phase3_policy_always_resolves_when_transformation_enabled(monkeypatch):
    calls: list[str] = []

    async def fake_resolve(question: str, history: list[dict]) -> StandaloneResolutionCall:
        calls.append(question)
        return StandaloneResolutionCall(query=question, failed=False, latency_ms=4)

    monkeypatch.setattr("services.query_service._call_standalone_resolution", fake_resolve)

    await transform_query(
        _STANDALONE,
        history=_HISTORY,
        resolution_policy=HistoryResolutionPolicy(run=False, use_gate=False, forced_skip_reason="disabled"),
    )
    assert calls == []

    await transform_query(
        _STANDALONE,
        history=_HISTORY,
        resolution_policy=HistoryResolutionPolicy(run=True, use_gate=False),
    )
    assert calls == [_STANDALONE]


@contextmanager
def _service_patches(embed):
    with ExitStack() as stack:
        for patcher in (
            patch("services.chat_service._resolve_retrieval_doc_ids", new=AsyncMock(return_value=["doc-1"])),
            patch("services.chat_service._retrieve_chunks_for_documents", new=AsyncMock(return_value=[])),
            patch("services.chat_service.get_embeddings", new=embed),
            patch("services.chat_service.generate_answer", new=AsyncMock(return_value=("ans", 1, "m"))),
            patch("db.get_session_history", new=AsyncMock(return_value=list(_HISTORY))),
            patch("db.store_chat_turn", new=AsyncMock()),
        ):
            stack.enter_context(patcher)
        yield


def _embed_capture(captured: dict):
    async def fake_embed(texts, **kwargs):
        captured["texts"] = list(texts)
        return [[0.1] for _ in texts]

    return fake_embed


@pytest.mark.asyncio
async def test_chat_pronoun_followup_resolves_when_transformation_disabled(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    captured: dict = {}

    async def fake_llm(system_instruction: str, user_text: str) -> str:
        captured["llm"] = True
        return _RESOLVED

    monkeypatch.setattr("services.query_service._llm_transform", fake_llm)
    with _service_patches(_embed_capture(captured)):
        result = await answer_question_for_document(
            question=_PRONOUN,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
            debug_retrieval=True,
        )

    assert captured["texts"] == [_RESOLVED]
    assert result["status"] == "ok"
    assert result["retrieval_debug"]["history_resolved_query"] == _RESOLVED
    assert result["retrieval_debug"]["history_resolution_applied"] is True


@pytest.mark.asyncio
async def test_chat_standalone_question_does_not_call_resolution_llm(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    captured: dict = {}

    async def resolve_must_not_run(question: str, history: list[dict]) -> StandaloneResolutionCall:
        raise AssertionError("standalone question must not call the resolution LLM")

    monkeypatch.setattr("services.query_service._call_standalone_resolution", resolve_must_not_run)
    with _service_patches(_embed_capture(captured)):
        result = await answer_question_for_document(
            question=_STANDALONE,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
            debug_retrieval=True,
        )

    assert captured["texts"] == [_STANDALONE]
    assert result["retrieval_debug"]["history_resolution_skipped_reason"] == "standalone_question"
    assert result["retrieval_debug"]["history_resolution_applied"] is False
    assert len(result["retrieval_debug"]["history_considered"]) == len(_HISTORY)
    assert result["retrieval_debug"]["history_window"]["retrieval"] == len(_HISTORY)


@pytest.mark.asyncio
async def test_chat_what_about_resolves_and_topic_switch_does_not(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    captured: dict = {"calls": 0}

    async def fake_resolve(question: str, history: list[dict]) -> StandaloneResolutionCall:
        captured["calls"] += 1
        captured["question"] = question
        return StandaloneResolutionCall(
            query="What does the second clause of the agreement say?",
            failed=False,
            latency_ms=2,
        )

    monkeypatch.setattr("services.query_service._call_standalone_resolution", fake_resolve)
    with _service_patches(_embed_capture(captured)):
        await answer_question_for_document(
            question=_WHAT_ABOUT,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
        )
    assert captured["calls"] == 1
    assert captured["texts"] == ["What does the second clause of the agreement say?"]

    captured["calls"] = 0
    with _service_patches(_embed_capture(captured)):
        await answer_question_for_document(
            question=_TOPIC_SWITCH,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
        )
    assert captured["calls"] == 0
    assert captured["texts"] == [_TOPIC_SWITCH]


@pytest.mark.asyncio
async def test_chat_resolution_failure_keeps_200_and_records_debug(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    captured: dict = {}

    async def fake_llm(system_instruction: str, user_text: str) -> None:
        return None

    monkeypatch.setattr("services.query_service._llm_transform", fake_llm)
    with _service_patches(_embed_capture(captured)):
        result = await answer_question_for_document(
            question=_PRONOUN,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
            debug_retrieval=True,
        )

    assert result["status"] == "ok"
    assert captured["texts"] == [_PRONOUN]
    assert result["retrieval_debug"]["history_resolution_fallback_reason"] == "llm_failure"
    assert "history_resolved_query" not in result["retrieval_debug"]


@pytest.mark.asyncio
async def test_history_aware_default_false_restores_phase3_gating(monkeypatch):
    _patch_process_flags(
        monkeypatch,
        HISTORY_AWARE_RETRIEVAL_DEFAULT=False,
        QUERY_TRANSFORMATION_ENABLED=False,
    )
    captured: dict = {}

    async def resolve_must_not_run(question: str, history: list[dict]) -> StandaloneResolutionCall:
        raise AssertionError("Phase 3 coupling must not resolve while transformation is off")

    monkeypatch.setattr("services.query_service._call_standalone_resolution", resolve_must_not_run)
    with _service_patches(_embed_capture(captured)):
        await answer_question_for_document(
            question=_PRONOUN,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
        )
    assert captured["texts"] == [_PRONOUN]

    _patch_process_flags(
        monkeypatch,
        HISTORY_AWARE_RETRIEVAL_DEFAULT=False,
        QUERY_TRANSFORMATION_ENABLED=True,
    )
    calls: list[str] = []

    async def fake_resolve(question: str, history: list[dict]) -> StandaloneResolutionCall:
        calls.append(question)
        return StandaloneResolutionCall(query=question, failed=False, latency_ms=1)

    monkeypatch.setattr("services.query_service._call_standalone_resolution", fake_resolve)
    monkeypatch.setattr("services.query_service._llm_transform", AsyncMock(return_value="rewritten"))
    with _service_patches(_embed_capture(captured)):
        await answer_question_for_document(
            question=_STANDALONE,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
        )
    assert calls == [_STANDALONE]


@pytest.mark.asyncio
async def test_chat_skip_history_transform_does_not_resolve(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    captured: dict = {}

    async def resolve_must_not_run(question: str, history: list[dict]) -> StandaloneResolutionCall:
        raise AssertionError("skip_history_transform must not resolve")

    monkeypatch.setattr("services.query_service._call_standalone_resolution", resolve_must_not_run)
    with _service_patches(_embed_capture(captured)):
        result = await answer_question_for_document(
            question=_PRONOUN,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
            debug_retrieval=True,
            retrieval_fields={"skip_history_transform": True},
        )

    assert captured["texts"] == [_PRONOUN]
    assert result["retrieval_debug"]["history_resolution_skipped_reason"] == "compare_skip"
    assert result["retrieval_debug"]["history_considered"] == []


@pytest.mark.asyncio
async def test_request_can_disable_resolution_and_windows_are_observable(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", True)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    full_history = [{"role": "user", "content": f"message-{i}"} for i in range(8)]
    captured: dict = {}

    async def resolve_must_not_run(question: str, history: list[dict]) -> StandaloneResolutionCall:
        raise AssertionError("explicit history_aware_retrieval=false must not resolve")

    async def fake_transform(question: str, history=None, **kwargs):
        captured["retrieval"] = history
        observation = kwargs["history_observation"]
        captured["generation_used"] = observation.generation_messages_used
        captured["config_applied"] = observation.config_applied
        return QueryTransformResult(queries=[question], original_query=question)

    def fake_context(chunks, session_context=None, **kwargs):
        captured["generation"] = list(session_context.chat_history) if session_context else []
        return "ctx"

    monkeypatch.setattr("services.query_service._call_standalone_resolution", resolve_must_not_run)
    with (
        patch("services.chat_service._resolve_retrieval_doc_ids", new=AsyncMock(return_value=["doc-1"])),
        patch("services.chat_service._retrieve_chunks_for_documents", new=AsyncMock(return_value=[])),
        patch("services.chat_service.get_embeddings", new=_embed_capture(captured)),
        patch("services.chat_service.generate_answer", new=AsyncMock(return_value=("ans", 1, "m"))),
        patch("services.chat_service.build_context_from_chunks", new=fake_context),
        patch("services.chat_service.transform_query", new=fake_transform),
        patch("db.get_session_history", new=AsyncMock(return_value=full_history)),
        patch("db.store_chat_turn", new=AsyncMock()),
    ):
        await answer_question_for_document(
            question=_PRONOUN,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
            history_fields={"history_aware_retrieval": False},
        )
        assert captured["retrieval"] is None

        await answer_question_for_document(
            question=_PRONOUN,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
            history_fields={"retrieval_history_window": 2, "generation_history_window": 4},
        )

    assert [msg["content"] for msg in captured["retrieval"]] == ["message-6", "message-7"]
    assert [msg["content"] for msg in captured["generation"]] == [
        "message-4",
        "message-5",
        "message-6",
        "message-7",
    ]
    assert captured["generation_used"] == 4
    assert captured["config_applied"]["retrieval_history_window"]["source"] == "request"


@pytest.mark.asyncio
async def test_retrieval_debug_history_considered_matches_retrieval_window(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    full_history = [{"role": "user", "content": f"message-{i}"} for i in range(8)]
    captured: dict = {}

    async def resolve_must_not_run(question: str, history: list[dict]) -> StandaloneResolutionCall:
        raise AssertionError("standalone question must not call the resolution LLM")

    monkeypatch.setattr("services.query_service._call_standalone_resolution", resolve_must_not_run)
    with (
        patch("services.chat_service._resolve_retrieval_doc_ids", new=AsyncMock(return_value=["doc-1"])),
        patch("services.chat_service._retrieve_chunks_for_documents", new=AsyncMock(return_value=[])),
        patch("services.chat_service.get_embeddings", new=_embed_capture(captured)),
        patch("services.chat_service.generate_answer", new=AsyncMock(return_value=("ans", 1, "m"))),
        patch("db.get_session_history", new=AsyncMock(return_value=full_history)),
        patch("db.store_chat_turn", new=AsyncMock()),
    ):
        result = await answer_question_for_document(
            question=_STANDALONE,
            doc_id="doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
            debug_retrieval=True,
            history_fields={"retrieval_history_window": 2},
        )

    debug = result["retrieval_debug"]
    assert [entry["content_preview"] for entry in debug["history_considered"]] == [
        "message-6",
        "message-7",
    ]
    assert debug["history_window"]["retrieval"] == 2
    assert debug["history_truncation"]["retrieval_messages_dropped"] == 6
    assert debug["history_truncation"]["max_context_chars"] > 0


@pytest.mark.asyncio
async def test_resolution_echo_omits_history_resolved_query_but_marks_applied(monkeypatch):
    async def fake_llm(system_instruction: str, user_text: str) -> str:
        return _STANDALONE

    monkeypatch.setattr("services.query_service._llm_transform", fake_llm)

    result = await transform_query(
        _STANDALONE,
        history=_HISTORY,
        resolution_policy=HistoryResolutionPolicy(run=True, use_gate=False),
    )

    debug = result.to_retrieval_debug()
    assert debug["history_resolution_applied"] is True
    assert "history_resolved_query" not in debug
    assert debug["history_resolution_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_stream_followup_uses_resolved_query(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    captured: dict = {}

    async def fake_llm(system_instruction: str, user_text: str) -> str:
        return _RESOLVED

    async def fake_stream(question, context):
        yield "token"

    provider = AsyncMock()
    provider.model_name = "test-model"
    monkeypatch.setattr("services.query_service._llm_transform", fake_llm)
    with (
        patch("services.chat_service._resolve_retrieval_doc_ids", new=AsyncMock(return_value=["doc-1"])),
        patch("services.chat_service._retrieve_chunks_for_documents", new=AsyncMock(return_value=[])),
        patch("services.chat_service.get_embeddings", new=_embed_capture(captured)),
        patch("services.chat_service.generate_answer_stream", new=fake_stream),
        patch("services.providers.get_llm_provider", return_value=provider),
        patch("db.get_session_history", new=AsyncMock(return_value=list(_HISTORY))),
        patch("db.store_chat_turn", new=AsyncMock()),
    ):
        async for _event in answer_question_stream_for_document(
            _PRONOUN,
            "doc-1",
            session_id="sess-1",
            auth=TEST_AUTH,
        ):
            pass

    assert captured["texts"] == [_RESOLVED]


@pytest.mark.asyncio
async def test_batch_compare_skips_history_and_multidoc_uses_it(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    captured: dict = {"contexts": []}

    async def fake_resolve(question: str, history: list[dict]) -> StandaloneResolutionCall:
        return StandaloneResolutionCall(query=_RESOLVED, failed=False, latency_ms=1)

    def fake_context(chunks, session_context=None, **kwargs):
        history = list(session_context.chat_history) if session_context and session_context.chat_history else []
        captured["contexts"].append(history)
        return "ctx"

    monkeypatch.setattr("services.query_service._call_standalone_resolution", fake_resolve)
    with (
        patch(
            "services.chat_service.get_embeddings",
            new=AsyncMock(side_effect=lambda texts, **kwargs: [[0.2] for _ in texts]),
        ),
        patch("services.chat_service._resolve_retrieval_doc_ids", new=AsyncMock(side_effect=lambda **kwargs: list(kwargs["requested_doc_ids"]))),
        patch("services.chat_service._retrieve_chunks_for_documents", new=AsyncMock(return_value=[])),
        patch("services.chat_service.build_context_from_chunks", new=fake_context),
        patch("services.chat_service.generate_answer", new=AsyncMock(return_value=("ans", 1, "m"))),
        patch("db.get_session_history", new=AsyncMock(return_value=list(_HISTORY))),
        patch("db.store_chat_turn", new=AsyncMock()) as store,
    ):
        await answer_questions_for_documents_batch(
            [{"question": _PRONOUN, "doc_ids": ["doc-1"], "session_id": "sess-1"}],
            auth=TEST_AUTH,
        )
        await answer_questions_for_documents_batch(
            [{"question": _PRONOUN, "doc_ids": ["doc-1", "doc-2"], "session_id": "sess-1"}],
            auth=TEST_AUTH,
        )

    assert captured["contexts"][0] == []
    assert captured["contexts"][1]
    assert store.await_count == 1


@pytest.mark.asyncio
async def test_eval_skips_history_unless_row_requests_simulation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "HISTORY_AWARE_RETRIEVAL_DEFAULT", True)
    calls: list[str] = []

    async def fake_resolve(question: str, history: list[dict]) -> StandaloneResolutionCall:
        calls.append(question)
        return StandaloneResolutionCall(query=_RESOLVED, failed=False, latency_ms=1)

    monkeypatch.setattr("services.query_service._call_standalone_resolution", fake_resolve)
    dataset = EvalDataset(
        name="sessions",
        queries=[
            EvalQuery(query=_PRONOUN, doc_ids=["doc-1"], relevant_doc_ids=["doc-1"]),
            EvalQuery(
                query=_PRONOUN,
                doc_ids=["doc-1"],
                relevant_doc_ids=["doc-1"],
                simulate_session=True,
                session_history=list(_HISTORY),
            ),
        ],
    )
    arm = EffectiveRetrievalConfig(
        label="default",
        hybrid_retrieval_enabled=False,
        enable_reranking=False,
    )
    with (
        patch("services.chat_service.find_similar_chunks", new=AsyncMock(return_value=[])),
        patch(
            "services.chat_service.get_embeddings",
            new=AsyncMock(side_effect=lambda texts, **kwargs: [[0.1] for _ in texts]),
        ),
    ):
        await run_eval(dataset, [arm], tenant_id="tenant-eval")

    assert calls == [_PRONOUN]

    path = tmp_path / "eval.json"
    path.write_text(
        json.dumps(
            {
                "name": "sim",
                "queries": [
                    {
                        "query": _PRONOUN,
                        "doc_ids": ["doc-1"],
                        "relevant_doc_ids": ["doc-1"],
                        "simulate_session": True,
                        "session_history": _HISTORY,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_dataset(path)
    assert loaded.queries[0].simulate_session is True
    assert loaded.queries[0].session_history[0]["role"] == "user"

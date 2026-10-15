"""Unit tests for request-scoped retrieval config resolution."""

import pytest

from services.retrieval_config import (
    InvalidRetrievalConfigError,
    RetrievalProcessDefaults,
    merge_retrieval_field_layers,
    resolve_retrieval_config,
)


def _env(**overrides) -> RetrievalProcessDefaults:
    base = dict(
        match_count=5,
        scope="session",
        hybrid_enabled=False,
        reranking_enabled=False,
        reranker_provider="similarity",
        query_transformation_enabled=False,
        query_transformation_strategy="rewrite",
        max_context_chars=32000,
    )
    base.update(overrides)
    return RetrievalProcessDefaults(**base)


def test_omitted_request_matches_process_defaults():
    env = _env(hybrid_enabled=False, reranking_enabled=False, query_transformation_enabled=False)
    effective = resolve_retrieval_config({}, env)

    assert effective.hybrid_enabled is False
    assert effective.reranking_enabled is False
    assert effective.query_transformation_enabled is False
    assert effective.apply_query_strategy is False
    assert effective.match_count == 5
    assert effective.scope == "session"
    assert all(source == "env" for name, source in effective.sources.items() if name != "skip_history_transform")
    assert effective.max_context_chars == 32000


def test_top_level_field_beats_retrieval_object_and_env():
    env = _env(hybrid_enabled=False, match_count=5)
    effective = resolve_retrieval_config(
        {
            "match_count": 3,
            "hybrid_enabled": False,
            "retrieval": {"match_count": 9, "hybrid_enabled": True, "reranking_enabled": True},
        },
        env,
    )

    assert effective.match_count == 3
    assert effective.sources["match_count"] == "request"
    assert effective.hybrid_enabled is False
    assert effective.sources["hybrid_enabled"] == "request"
    assert effective.reranking_enabled is True
    assert effective.sources["reranking_enabled"] == "request"


def test_retrieval_object_beats_env_when_top_level_omitted():
    env = _env(hybrid_enabled=False, reranking_enabled=False)
    effective = resolve_retrieval_config(
        {"retrieval": {"hybrid_enabled": True}},
        env,
    )

    assert effective.hybrid_enabled is True
    assert effective.sources["hybrid_enabled"] == "request"
    assert effective.reranking_enabled is False
    assert effective.sources["reranking_enabled"] == "env"


def test_strategy_other_than_none_enables_transformation_when_env_disabled():
    env = _env(query_transformation_enabled=False, query_transformation_strategy="rewrite")
    effective = resolve_retrieval_config(
        {"query_transformation_strategy": "rewrite"},
        env,
    )

    assert effective.query_transformation_enabled is True
    assert effective.sources["query_transformation_enabled"] == "request"
    assert effective.query_transformation_strategy == "rewrite"
    assert effective.apply_query_strategy is True


def test_explicit_disable_beats_strategy():
    env = _env(query_transformation_enabled=True, query_transformation_strategy="expand")
    effective = resolve_retrieval_config(
        {
            "query_transformation_enabled": False,
            "query_transformation_strategy": "rewrite",
        },
        env,
    )

    assert effective.query_transformation_enabled is False
    assert effective.apply_query_strategy is False
    assert effective.query_transformation_strategy == "rewrite"


def test_enabled_true_with_strategy_none_does_not_apply_strategy():
    env = _env(query_transformation_enabled=False, query_transformation_strategy="rewrite")
    effective = resolve_retrieval_config(
        {
            "query_transformation_enabled": True,
            "query_transformation_strategy": "none",
        },
        env,
    )

    assert effective.query_transformation_enabled is True
    assert effective.query_transformation_strategy == "none"
    assert effective.apply_query_strategy is False


def test_strategy_not_enabled_by_omission_alone():
    env = _env(query_transformation_enabled=False, query_transformation_strategy="expand")
    effective = resolve_retrieval_config({}, env)

    assert effective.query_transformation_enabled is False
    assert effective.apply_query_strategy is False
    assert effective.query_transformation_strategy == "expand"
    assert effective.sources["query_transformation_strategy"] == "env"


def test_env_enabled_strategy_applies_when_request_omits_overrides():
    env = _env(query_transformation_enabled=True, query_transformation_strategy="stepback")
    effective = resolve_retrieval_config({}, env)

    assert effective.query_transformation_enabled is True
    assert effective.apply_query_strategy is True
    assert effective.sources["query_transformation_enabled"] == "env"


def test_debug_dict_records_value_and_source_and_context_annex():
    env = _env(max_context_chars=1234)
    effective = resolve_retrieval_config({"retrieval": {"hybrid_enabled": True}}, env)
    debug = effective.to_debug_dict()

    assert debug["hybrid_enabled"] == {"value": True, "source": "request"}
    assert debug["reranking_enabled"]["source"] == "env"
    assert debug["max_context_chars"] == {"value": 1234, "source": "env"}
    assert "skip_history_transform" not in debug


def test_merge_layers_item_top_beats_item_retrieval_and_batch():
    merged = merge_retrieval_field_layers(
        {"match_count": 3, "retrieval": {"match_count": 9, "hybrid_enabled": True}},
        {"match_count": 7, "retrieval": {"reranking_enabled": True}},
    )
    effective = resolve_retrieval_config(merged, _env())

    assert effective.match_count == 3
    assert effective.hybrid_enabled is True
    assert effective.reranking_enabled is True


def test_merge_layers_item_retrieval_beats_batch_top_level():
    merged = merge_retrieval_field_layers(
        {"retrieval": {"scope": "session"}},
        {"scope": "tenant"},
    )
    effective = resolve_retrieval_config(merged, _env())

    assert effective.scope == "session"
    assert effective.sources["scope"] == "request"


def test_invalid_strategy_and_reranker_raise():
    env = _env()
    with pytest.raises(InvalidRetrievalConfigError):
        resolve_retrieval_config({"query_transformation_strategy": "bogus"}, env)
    with pytest.raises(InvalidRetrievalConfigError):
        resolve_retrieval_config({"reranker_provider": "external"}, env)


def test_resolve_does_not_mutate_process_defaults():
    env = _env(hybrid_enabled=False)
    before = env.hybrid_enabled
    resolve_retrieval_config({"hybrid_enabled": True}, env)
    assert env.hybrid_enabled is before is False

"""History config resolution, windows, and the Phase 3 / Phase 4 policy split."""

import pytest

from services.history_config import (
    HistoryProcessDefaults,
    InvalidHistoryConfigError,
    MAX_HISTORY_WINDOW,
    build_resolution_policy,
    history_load_limit,
    merge_history_fields,
    prepare_turn_history,
    resolve_history_config,
    trim_generation_history,
)


def _env(**overrides) -> HistoryProcessDefaults:
    values = dict(
        retrieval_history_window=6,
        generation_history_window=10,
        history_aware_retrieval=True,
        max_context_chars=32000,
    )
    values.update(overrides)
    return HistoryProcessDefaults(**values)


def test_omitted_history_config_uses_process_defaults():
    effective = resolve_history_config(None, _env())

    assert effective.retrieval_history_window == 6
    assert effective.generation_history_window == 10
    assert effective.history_aware_retrieval is True
    assert effective.max_generation_history_chars is None
    assert effective.sources["history_aware_retrieval"] == "env"
    assert effective.config_applied_debug() is None


def test_request_history_fields_override_defaults():
    effective = resolve_history_config(
        {
            "retrieval_history_window": 2,
            "generation_history_window": 4,
            "history_aware_retrieval": False,
            "max_generation_history_chars": 50,
        },
        _env(),
    )

    assert effective.retrieval_history_window == 2
    assert effective.generation_history_window == 4
    assert effective.history_aware_retrieval is False
    assert effective.max_generation_history_chars == 50
    assert all(source == "request" for source in effective.sources.values())
    applied = effective.config_applied_debug()
    assert applied is not None
    assert applied["retrieval_history_window"]["value"] == 2


def test_process_window_above_cap_is_clamped():
    effective = resolve_history_config(
        None,
        _env(retrieval_history_window=500, generation_history_window=500),
    )
    assert effective.retrieval_history_window == 100
    assert effective.generation_history_window == 100
    assert effective.sources["retrieval_history_window"] == "env"


def test_invalid_window_and_char_cap_raise():
    env = _env()
    with pytest.raises(InvalidHistoryConfigError):
        resolve_history_config({"retrieval_history_window": -1}, env)
    with pytest.raises(InvalidHistoryConfigError):
        resolve_history_config({"generation_history_window": 101}, env)
    with pytest.raises(InvalidHistoryConfigError):
        resolve_history_config({"max_generation_history_chars": 0}, env)


def test_merge_history_fields_item_wins_over_batch():
    merged = merge_history_fields(
        {"retrieval_history_window": 2},
        {"retrieval_history_window": 8, "generation_history_window": 3},
    )
    assert merged == {"retrieval_history_window": 2, "generation_history_window": 3}


def test_explicit_false_never_resolves_but_env_false_restores_phase3():
    explicit = resolve_history_config({"history_aware_retrieval": False}, _env())
    policy = build_resolution_policy(
        explicit,
        skip_history_transform=False,
        global_query_transformation_enabled=True,
    )
    assert policy.run is False
    assert policy.forced_skip_reason == "disabled"

    phase3 = resolve_history_config(None, _env(history_aware_retrieval=False))
    off = build_resolution_policy(
        phase3,
        skip_history_transform=False,
        global_query_transformation_enabled=False,
    )
    assert off.run is False
    assert off.forced_skip_reason == "disabled"

    on = build_resolution_policy(
        phase3,
        skip_history_transform=False,
        global_query_transformation_enabled=True,
    )
    assert on.run is True
    assert on.use_gate is False

    forced = resolve_history_config(
        {"history_aware_retrieval": True},
        _env(history_aware_retrieval=False),
    )
    gated = build_resolution_policy(
        forced,
        skip_history_transform=False,
        global_query_transformation_enabled=False,
    )
    assert gated.run is True
    assert gated.use_gate is True


def test_trim_generation_history_drops_oldest_then_trims_tail():
    messages = [
        {"role": "user", "content": "aaaa"},
        {"role": "assistant", "content": "bbbb"},
        {"role": "user", "content": "cccc"},
    ]
    kept, truncated = trim_generation_history(messages, 6)
    assert truncated is True
    assert [msg["content"] for msg in kept] == ["bb", "cccc"]
    assert messages[1]["content"] == "bbbb"


def test_prepare_turn_history_splits_retrieval_and_generation_windows():
    history = [{"role": "user", "content": f"m{i}"} for i in range(8)]
    plan = prepare_turn_history(
        history,
        {"retrieval_history_window": 2, "generation_history_window": 4},
        skip_history_transform=False,
        apply_generation_history=True,
        global_query_transformation_enabled=False,
        process_env=_env(),
    )

    assert [msg["content"] for msg in plan.transform_history] == ["m6", "m7"]
    assert [msg["content"] for msg in plan.generation_history] == ["m4", "m5", "m6", "m7"]
    assert plan.policy.use_gate is True
    assert plan.observation.retrieval_messages_used == 2
    assert plan.observation.generation_messages_used == 4
    assert plan.observation.retrieval_messages_dropped == 6


def test_history_load_limit_honors_requested_windows_up_to_cap():
    env = _env()
    assert history_load_limit(None, process_env=env, session_load_floor=20) == 20
    assert history_load_limit(
        {"retrieval_history_window": 50},
        process_env=env,
        session_load_floor=20,
    ) == 50
    assert history_load_limit(
        {"generation_history_window": 100},
        process_env=env,
        session_load_floor=20,
    ) == MAX_HISTORY_WINDOW
    assert history_load_limit(
        {"retrieval_history_window": 0, "generation_history_window": 0},
        process_env=env,
        session_load_floor=20,
    ) == 20


def test_compare_skip_clears_transform_and_generation_history():
    history = [{"role": "user", "content": "earlier"}]
    plan = prepare_turn_history(
        history,
        None,
        skip_history_transform=True,
        apply_generation_history=False,
        global_query_transformation_enabled=False,
        process_env=_env(),
    )
    assert plan.transform_history is None
    assert plan.generation_history == []
    assert plan.policy.forced_skip_reason == "compare_skip"
    assert plan.observation.considered == []

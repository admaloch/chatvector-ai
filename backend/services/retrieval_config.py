"""Request-scoped retrieval configuration.

Resolves optional chat/stream/batch overrides into an immutable effective
config. Process env and the config singleton are read, never written.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

VALID_QUERY_STRATEGIES = {"rewrite", "expand", "stepback", "none"}
_OVERRIDE_FIELDS = (
    "match_count",
    "scope",
    "hybrid_enabled",
    "reranking_enabled",
    "reranker_provider",
    "query_transformation_enabled",
    "query_transformation_strategy",
    "skip_history_transform",
)
_DEBUG_FIELDS = (
    "match_count",
    "scope",
    "hybrid_enabled",
    "reranking_enabled",
    "reranker_provider",
    "query_transformation_enabled",
    "query_transformation_strategy",
)


class InvalidRetrievalConfigError(ValueError):
    """Raised when a request retrieval override cannot be resolved."""


@dataclass(frozen=True)
class RetrievalProcessDefaults:
    """Process-level retrieval defaults. Not mutated per request."""

    match_count: int = 5
    scope: str = "session"
    hybrid_enabled: bool = False
    reranking_enabled: bool = False
    reranker_provider: str = "similarity"
    query_transformation_enabled: bool = False
    query_transformation_strategy: str = "rewrite"
    max_context_chars: int = 32000

    @classmethod
    def from_process(cls) -> "RetrievalProcessDefaults":
        from core.config import config
        from services.context_service import MAX_CONTEXT_CHARS

        return cls(
            hybrid_enabled=bool(config.HYBRID_RETRIEVAL_ENABLED),
            reranking_enabled=bool(config.ENABLE_RERANKING),
            reranker_provider=str(config.RERANKER_PROVIDER),
            query_transformation_enabled=bool(config.QUERY_TRANSFORMATION_ENABLED),
            query_transformation_strategy=str(config.QUERY_TRANSFORMATION_STRATEGY),
            max_context_chars=int(MAX_CONTEXT_CHARS),
        )


@dataclass(frozen=True)
class EffectiveRetrievalConfig:
    """Immutable retrieval settings for a single request."""

    match_count: int
    scope: str
    hybrid_enabled: bool
    reranking_enabled: bool
    reranker_provider: str
    query_transformation_enabled: bool
    query_transformation_strategy: str
    apply_query_strategy: bool
    sources: Mapping[str, str]
    skip_history_transform: bool = False
    max_context_chars: int = 32000

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))

    def to_debug_dict(self) -> dict[str, dict[str, Any]]:
        """Resolved values plus request-vs-env source for debug and eval snapshots."""
        payload: dict[str, dict[str, Any]] = {}
        for name in _DEBUG_FIELDS:
            payload[name] = {
                "value": getattr(self, name),
                "source": self.sources[name],
            }
        payload["max_context_chars"] = {
            "value": self.max_context_chars,
            "source": "env",
        }
        if self.sources.get("skip_history_transform") == "request":
            payload["skip_history_transform"] = {
                "value": self.skip_history_transform,
                "source": "request",
            }
        return payload


def transformation_is_request_scoped(effective: EffectiveRetrievalConfig) -> bool:
    """True when this request set a query-transformation override."""
    return (
        effective.sources.get("query_transformation_enabled") == "request"
        or effective.sources.get("query_transformation_strategy") == "request"
    )


def merge_retrieval_field_layers(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """Merge retrieval request layers. Earlier layers outrank later layers.

    Within a layer, top-level fields outrank that layer's ``retrieval`` object.
    An earlier layer's nested object still outranks a later layer's top-level fields.
    """
    acc_top: dict[str, Any] = {}
    acc_nested: dict[str, Any] = {}
    for layer in reversed(layers):
        low_nested = {
            key: value
            for key, value in dict(layer.get("retrieval") or {}).items()
            if key in _OVERRIDE_FIELDS
        }
        low_top = {
            key: value
            for key, value in layer.items()
            if key != "retrieval" and key in _OVERRIDE_FIELDS
        }
        new_nested: dict[str, Any] = {}
        new_nested.update(acc_nested)
        new_nested.update(acc_top)
        new_nested.update(low_nested)
        acc_nested = new_nested
        acc_top = low_top

    result = dict(acc_top)
    nested = {key: value for key, value in acc_nested.items() if key not in acc_top}
    if nested:
        result["retrieval"] = nested
    return result


def _layer_values(request_fields: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    top = {
        key: request_fields[key]
        for key in _OVERRIDE_FIELDS
        if key in request_fields and request_fields[key] is not None
    }
    nested_raw = request_fields.get("retrieval") or {}
    nested = {
        key: nested_raw[key]
        for key in _OVERRIDE_FIELDS
        if key in nested_raw and nested_raw[key] is not None
    }
    return top, nested


def _pick(name: str, top: Mapping[str, Any], nested: Mapping[str, Any], env_value: Any) -> tuple[Any, str]:
    if name in top:
        return top[name], "request"
    if name in nested:
        return nested[name], "request"
    return env_value, "env"


def _resolve_query_transformation(
    *,
    req_enabled: bool | None,
    req_strategy: str | None,
    env_enabled: bool,
    env_strategy: str,
) -> tuple[bool, str, str, str, bool]:
    """Return enabled, enabled_source, strategy, strategy_source, apply_strategy.

    Model A:
    - a strategy other than ``none`` enables transformation even when the
      process flag is false
    - ``query_transformation_enabled=false`` disables transformation even
      when a strategy is set
    - ``query_transformation_enabled=true`` with strategy ``none`` keeps
      transformation enabled for resolution but does not apply a strategy
    - rewrite / expand / step-back are not enabled by omission alone
    """
    if req_strategy is not None:
        strategy = str(req_strategy).strip().lower()
        strategy_source = "request"
        if strategy not in VALID_QUERY_STRATEGIES:
            raise InvalidRetrievalConfigError(
                f"Invalid query_transformation_strategy={req_strategy!r}. "
                f"Expected one of: {', '.join(sorted(VALID_QUERY_STRATEGIES))}."
            )
    else:
        strategy = env_strategy
        strategy_source = "env"

    if req_enabled is False:
        return False, "request", strategy, strategy_source, False
    if req_enabled is True:
        return True, "request", strategy, strategy_source, strategy != "none"
    if req_strategy is not None and strategy != "none":
        return True, "request", strategy, strategy_source, True
    if strategy == "none":
        return bool(env_enabled), "env", strategy, strategy_source, False
    return bool(env_enabled), "env", strategy, strategy_source, bool(env_enabled)


def resolve_retrieval_config(
    request_fields: Mapping[str, Any] | None = None,
    process_env: RetrievalProcessDefaults | None = None,
) -> EffectiveRetrievalConfig:
    """Resolve request overrides over process defaults.

    Precedence for each field: explicit top-level request field, then the
    nested ``retrieval`` object, then ``process_env``.
    """
    from services.reranker import VALID_RERANKER_PROVIDERS

    env = process_env if process_env is not None else RetrievalProcessDefaults.from_process()
    top, nested = _layer_values(request_fields or {})

    match_count, match_source = _pick("match_count", top, nested, env.match_count)
    try:
        match_count = int(match_count)
    except (TypeError, ValueError) as exc:
        raise InvalidRetrievalConfigError(
            f"Invalid match_count={match_count!r}."
        ) from exc
    if match_count < 1 or match_count > 20:
        raise InvalidRetrievalConfigError(
            f"Invalid match_count={match_count}. Expected an integer from 1 to 20."
        )

    scope, scope_source = _pick("scope", top, nested, env.scope)
    scope = str(scope).strip().lower()
    if scope not in {"session", "tenant"}:
        raise InvalidRetrievalConfigError(
            f"Invalid retrieval scope={scope!r}. Must be one of: session, tenant."
        )

    hybrid_enabled, hybrid_source = _pick("hybrid_enabled", top, nested, env.hybrid_enabled)
    reranking_enabled, reranking_source = _pick(
        "reranking_enabled", top, nested, env.reranking_enabled
    )
    reranker_provider, reranker_source = _pick(
        "reranker_provider", top, nested, env.reranker_provider
    )
    reranker_provider = str(reranker_provider).strip().lower()
    if reranker_source == "request" and reranker_provider not in VALID_RERANKER_PROVIDERS:
        raise InvalidRetrievalConfigError(
            f"Unknown reranker_provider={reranker_provider!r}. "
            f"Expected one of: {', '.join(sorted(VALID_RERANKER_PROVIDERS))}."
        )

    req_enabled = top["query_transformation_enabled"] if "query_transformation_enabled" in top else (
        nested["query_transformation_enabled"] if "query_transformation_enabled" in nested else None
    )
    req_strategy = top["query_transformation_strategy"] if "query_transformation_strategy" in top else (
        nested["query_transformation_strategy"] if "query_transformation_strategy" in nested else None
    )
    (
        qt_enabled,
        qt_enabled_source,
        qt_strategy,
        qt_strategy_source,
        apply_strategy,
    ) = _resolve_query_transformation(
        req_enabled=req_enabled if isinstance(req_enabled, bool) or req_enabled is None else bool(req_enabled),
        req_strategy=None if req_strategy is None else str(req_strategy),
        env_enabled=env.query_transformation_enabled,
        env_strategy=env.query_transformation_strategy,
    )

    skip_history, skip_source = _pick("skip_history_transform", top, nested, False)

    sources = {
        "match_count": match_source,
        "scope": scope_source,
        "hybrid_enabled": hybrid_source,
        "reranking_enabled": reranking_source,
        "reranker_provider": reranker_source,
        "query_transformation_enabled": qt_enabled_source,
        "query_transformation_strategy": qt_strategy_source,
        "skip_history_transform": skip_source,
    }
    return EffectiveRetrievalConfig(
        match_count=match_count,
        scope=scope,
        hybrid_enabled=bool(hybrid_enabled),
        reranking_enabled=bool(reranking_enabled),
        reranker_provider=reranker_provider,
        query_transformation_enabled=qt_enabled,
        query_transformation_strategy=qt_strategy,
        apply_query_strategy=apply_strategy,
        sources=sources,
        skip_history_transform=bool(skip_history),
        max_context_chars=env.max_context_chars,
    )

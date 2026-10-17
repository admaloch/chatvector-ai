"""Request-scoped history configuration.

Resolves optional chat, stream, and batch ``history`` overrides. Process env
and the config singleton are read, never written.

Standalone resolution is not a retrieval strategy. ``history_aware_retrieval``
does not enable rewrite, expand, or step-back.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

HISTORY_FIELD_NAMES = (
    "retrieval_history_window",
    "generation_history_window",
    "history_aware_retrieval",
    "max_generation_history_chars",
)
MAX_HISTORY_WINDOW = 100
MAX_GENERATION_HISTORY_CHARS = 1_000_000
HISTORY_PREVIEW_CHARS = 160


class InvalidHistoryConfigError(ValueError):
    """Raised when a request history override cannot be resolved."""


@dataclass(frozen=True)
class HistoryProcessDefaults:
    """Process-level history defaults. Not mutated per request."""

    retrieval_history_window: int = 6
    generation_history_window: int = 10
    history_aware_retrieval: bool = True
    max_context_chars: int = 32000

    @classmethod
    def from_process(cls) -> "HistoryProcessDefaults":
        from core.config import config
        from services.context_service import MAX_CONTEXT_CHARS

        return cls(
            retrieval_history_window=int(config.QUERY_TRANSFORMATION_HISTORY_WINDOW),
            generation_history_window=int(config.GENERATION_HISTORY_WINDOW),
            history_aware_retrieval=bool(config.HISTORY_AWARE_RETRIEVAL_DEFAULT),
            max_context_chars=int(MAX_CONTEXT_CHARS),
        )


@dataclass(frozen=True)
class EffectiveHistoryConfig:
    """Immutable history settings for a single request."""

    retrieval_history_window: int
    generation_history_window: int
    history_aware_retrieval: bool
    max_generation_history_chars: int | None
    max_context_chars: int
    sources: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))

    def config_applied_debug(self) -> dict[str, dict[str, Any]] | None:
        """Resolved history settings when the request set at least one field."""
        if not any(self.sources.get(name) == "request" for name in HISTORY_FIELD_NAMES):
            return None
        payload: dict[str, dict[str, Any]] = {}
        for name in (
            "retrieval_history_window",
            "generation_history_window",
            "history_aware_retrieval",
            "max_generation_history_chars",
        ):
            payload[name] = {
                "value": getattr(self, name),
                "source": self.sources[name],
            }
        return payload


@dataclass(frozen=True)
class HistoryResolutionPolicy:
    """Whether standalone resolution may run, and whether the gate applies.

    ``run=False`` never calls the resolution LLM.

    ``run=True`` and ``use_gate=True`` is the Phase 4 default. An extra LLM
    call happens only when the deterministic need-resolution gate says the
    question depends on prior turns.

    ``run=True`` and ``use_gate=False`` restores Phase 3: non-empty history
    always resolves. That path is used when ``HISTORY_AWARE_RETRIEVAL_DEFAULT``
    is false (and the request did not explicitly enable history-aware
    retrieval) while ``QUERY_TRANSFORMATION_ENABLED`` is true.
    """

    run: bool
    use_gate: bool
    forced_skip_reason: str | None = None


@dataclass(frozen=True)
class HistoryObservation:
    """Window sizes and messages actually fed into resolution or generation."""

    considered: list[dict]
    retrieval_messages_used: int
    generation_messages_used: int
    retrieval_messages_dropped: int
    generation_messages_dropped: int
    generation_chars_truncated: bool
    config_applied: dict[str, dict[str, Any]] | None
    max_context_chars: int
    max_generation_history_chars: int | None


@dataclass(frozen=True)
class TurnHistoryPlan:
    """Retrieval slice, generation slice, and the resolution policy for one turn."""

    transform_history: list[dict] | None
    generation_history: list[dict]
    policy: HistoryResolutionPolicy
    observation: HistoryObservation


def build_resolution_policy(
    effective: EffectiveHistoryConfig,
    *,
    skip_history_transform: bool,
    global_query_transformation_enabled: bool,
) -> HistoryResolutionPolicy:
    """Choose Phase 4 gating, Phase 3 always-resolve, or no resolution.

    Explicit ``history_aware_retrieval=false`` never resolves, even when
    query transformation is enabled. Omitting the field while
    ``HISTORY_AWARE_RETRIEVAL_DEFAULT=false`` restores Phase 3 coupling:
    resolution runs only when the process ``QUERY_TRANSFORMATION_ENABLED``
    flag is true, and then on every non-empty history window.
    """
    if skip_history_transform:
        return HistoryResolutionPolicy(run=False, use_gate=False, forced_skip_reason="compare_skip")
    source = effective.sources.get("history_aware_retrieval", "env")
    if source == "request" and not effective.history_aware_retrieval:
        return HistoryResolutionPolicy(run=False, use_gate=False, forced_skip_reason="disabled")
    if effective.history_aware_retrieval:
        return HistoryResolutionPolicy(run=True, use_gate=True, forced_skip_reason=None)
    if global_query_transformation_enabled:
        return HistoryResolutionPolicy(run=True, use_gate=False, forced_skip_reason=None)
    return HistoryResolutionPolicy(run=False, use_gate=False, forced_skip_reason="disabled")


def _bounded_window(name: str, value: Any, *, strict: bool) -> int:
    try:
        window = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidHistoryConfigError(f"Invalid {name}={value!r}.") from exc
    if window < 0 or window > MAX_HISTORY_WINDOW:
        if not strict:
            return max(0, min(window, MAX_HISTORY_WINDOW))
        raise InvalidHistoryConfigError(
            f"Invalid {name}={window}. Expected an integer from 0 to {MAX_HISTORY_WINDOW}."
        )
    return window


def _bounded_chars(value: Any) -> int | None:
    if value is None:
        return None
    try:
        chars = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidHistoryConfigError(
            f"Invalid max_generation_history_chars={value!r}."
        ) from exc
    if chars < 1 or chars > MAX_GENERATION_HISTORY_CHARS:
        raise InvalidHistoryConfigError(
            f"Invalid max_generation_history_chars={chars}. "
            f"Expected an integer from 1 to {MAX_GENERATION_HISTORY_CHARS}."
        )
    return chars


def resolve_history_config(
    request_fields: Mapping[str, Any] | None = None,
    process_env: HistoryProcessDefaults | None = None,
) -> EffectiveHistoryConfig:
    """Resolve request history overrides over process defaults."""
    env = process_env if process_env is not None else HistoryProcessDefaults.from_process()
    fields = request_fields or {}

    if "retrieval_history_window" in fields and fields["retrieval_history_window"] is not None:
        retrieval_window = _bounded_window(
            "retrieval_history_window", fields["retrieval_history_window"], strict=True
        )
        retrieval_source = "request"
    else:
        retrieval_window = _bounded_window(
            "retrieval_history_window", env.retrieval_history_window, strict=False
        )
        retrieval_source = "env"

    if "generation_history_window" in fields and fields["generation_history_window"] is not None:
        generation_window = _bounded_window(
            "generation_history_window", fields["generation_history_window"], strict=True
        )
        generation_source = "request"
    else:
        generation_window = _bounded_window(
            "generation_history_window", env.generation_history_window, strict=False
        )
        generation_source = "env"

    if "history_aware_retrieval" in fields and fields["history_aware_retrieval"] is not None:
        history_aware = bool(fields["history_aware_retrieval"])
        history_source = "request"
    else:
        history_aware = bool(env.history_aware_retrieval)
        history_source = "env"

    if (
        "max_generation_history_chars" in fields
        and fields["max_generation_history_chars"] is not None
    ):
        max_chars = _bounded_chars(fields["max_generation_history_chars"])
        chars_source = "request"
    else:
        max_chars = None
        chars_source = "env"

    return EffectiveHistoryConfig(
        retrieval_history_window=retrieval_window,
        generation_history_window=generation_window,
        history_aware_retrieval=history_aware,
        max_generation_history_chars=max_chars,
        max_context_chars=env.max_context_chars,
        sources={
            "retrieval_history_window": retrieval_source,
            "generation_history_window": generation_source,
            "history_aware_retrieval": history_source,
            "max_generation_history_chars": chars_source,
        },
    )


def slice_history(history: list[dict], window: int) -> list[dict]:
    """Keep the most recent ``window`` messages. Zero yields an empty slice."""
    if not history or window <= 0:
        return []
    return list(history[-window:])


def trim_generation_history(
    messages: list[dict],
    max_chars: int | None,
) -> tuple[list[dict], bool]:
    """Fit generation history to a character budget before chunk truncation.

    Counts message ``content`` characters. Drops oldest messages first. If the
    newest message alone exceeds the budget, its content is trimmed to the
    tail. ``None`` applies no extra cap. Does not mutate the input messages.
    """
    if max_chars is None or not messages:
        return list(messages), False

    kept: list[dict] = []
    total = 0
    truncated = False
    for msg in reversed(messages):
        content = msg.get("content") or ""
        remaining = max_chars - total
        if remaining <= 0:
            truncated = True
            break
        if len(content) <= remaining:
            kept.append(msg)
            total += len(content)
            continue
        trimmed = dict(msg)
        trimmed["content"] = content[-remaining:]
        kept.append(trimmed)
        truncated = True
        break
    kept.reverse()
    if len(kept) < len(messages):
        truncated = True
    return kept, truncated


def prepare_turn_history(
    history: list[dict] | None,
    request_fields: Mapping[str, Any] | None,
    *,
    skip_history_transform: bool,
    apply_generation_history: bool,
    global_query_transformation_enabled: bool,
    process_env: HistoryProcessDefaults | None = None,
) -> TurnHistoryPlan:
    """Build the retrieval window, generation window, and resolution policy."""
    loaded = list(history or [])
    effective = resolve_history_config(request_fields, process_env)
    policy = build_resolution_policy(
        effective,
        skip_history_transform=skip_history_transform,
        global_query_transformation_enabled=global_query_transformation_enabled,
    )
    retrieval_slice = slice_history(loaded, effective.retrieval_history_window)
    generation_raw = slice_history(loaded, effective.generation_history_window)
    generation_slice, chars_truncated = trim_generation_history(
        generation_raw,
        effective.max_generation_history_chars,
    )

    if policy.run:
        transform_history = retrieval_slice or None
        considered = retrieval_slice
        retrieval_used = len(retrieval_slice)
        retrieval_dropped = max(0, len(loaded) - len(retrieval_slice))
    else:
        transform_history = None
        considered = []
        retrieval_used = 0
        retrieval_dropped = 0

    if apply_generation_history:
        generation_used = len(generation_slice)
        generation_dropped = max(0, len(loaded) - len(generation_slice))
    else:
        generation_slice = []
        chars_truncated = False
        generation_used = 0
        generation_dropped = 0

    observation = HistoryObservation(
        considered=considered,
        retrieval_messages_used=retrieval_used,
        generation_messages_used=generation_used,
        retrieval_messages_dropped=retrieval_dropped,
        generation_messages_dropped=generation_dropped,
        generation_chars_truncated=chars_truncated,
        config_applied=effective.config_applied_debug(),
        max_context_chars=effective.max_context_chars,
        max_generation_history_chars=effective.max_generation_history_chars,
    )
    return TurnHistoryPlan(
        transform_history=transform_history,
        generation_history=generation_slice,
        policy=policy,
        observation=observation,
    )


def merge_history_fields(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """Merge history request layers. Earlier layers outrank later layers."""
    merged: dict[str, Any] = {}
    for layer in reversed(layers):
        for key, value in layer.items():
            if key in HISTORY_FIELD_NAMES and value is not None:
                merged[key] = value
    return merged

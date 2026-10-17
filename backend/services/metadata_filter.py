"""Validation for the request ``metadata_filter`` retrieval override.

v1 grammar (keys are document metadata field names; values are scalars, i.e.
string, number, or boolean, matching what ``POST /upload`` stores):

- exact match:   ``{"department": "legal"}``
- scalar in-list: ``{"department": {"in": ["legal", "hr"]}}``
- multiple keys are ANDed

Nested objects, arrays as operands, ``null``, and any other operator are
rejected. Matching is same-type; strings are case-sensitive.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

from core.config import config

IN_OPERATOR = "in"


class InvalidMetadataFilterError(ValueError):
    """Raised when a ``metadata_filter`` does not match the v1 grammar."""


def _validate_scalar(key: str, value: Any) -> None:
    if isinstance(value, str):
        if "\x00" in value:
            raise InvalidMetadataFilterError(
                f"metadata_filter value for '{key}' must not contain NUL characters."
            )
    elif isinstance(value, bool):
        return
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidMetadataFilterError(
                f"metadata_filter value for '{key}' must be a finite number."
            )
    else:
        raise InvalidMetadataFilterError(
            f"metadata_filter value for '{key}' must be a string, number, boolean, "
            f"or an {{'{IN_OPERATOR}': [...]}} list of scalars."
        )


def validate_metadata_filter(raw: Any) -> dict[str, Any]:
    """Validate a v1 metadata filter and return a normalized copy.

    Raises ``InvalidMetadataFilterError`` (a ``ValueError``) when malformed.
    """
    if not isinstance(raw, Mapping):
        raise InvalidMetadataFilterError("metadata_filter must be a JSON object.")
    if not raw:
        raise InvalidMetadataFilterError("metadata_filter must contain at least one key.")
    if len(raw) > config.DOCUMENT_METADATA_MAX_KEYS:
        raise InvalidMetadataFilterError(
            f"metadata_filter may contain at most {config.DOCUMENT_METADATA_MAX_KEYS} keys."
        )

    normalized: dict[str, Any] = {}
    for key, operand in raw.items():
        if not isinstance(key, str) or not key or "\x00" in key:
            raise InvalidMetadataFilterError(
                "metadata_filter keys must be non-empty strings without NUL characters."
            )
        if isinstance(operand, Mapping):
            if set(operand) != {IN_OPERATOR}:
                raise InvalidMetadataFilterError(
                    f"metadata_filter for '{key}' only supports the '{IN_OPERATOR}' operator."
                )
            values = operand[IN_OPERATOR]
            if not isinstance(values, list) or not values:
                raise InvalidMetadataFilterError(
                    f"metadata_filter '{IN_OPERATOR}' for '{key}' must be a non-empty list of scalars."
                )
            for value in values:
                _validate_scalar(key, value)
            normalized[key] = {IN_OPERATOR: list(values)}
        else:
            _validate_scalar(key, operand)
            normalized[key] = operand

    try:
        size = len(json.dumps(normalized, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise InvalidMetadataFilterError("metadata_filter must be valid JSON.") from None
    if size > config.DOCUMENT_METADATA_MAX_BYTES:
        raise InvalidMetadataFilterError(
            f"metadata_filter exceeds the maximum size of {config.DOCUMENT_METADATA_MAX_BYTES} bytes."
        )
    return normalized

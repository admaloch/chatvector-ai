import json
import math

from core.config import config
from services.ingestion_pipeline import UploadPipelineError

_STAGE = "validation"


def _invalid(code: str, message: str) -> UploadPipelineError:
    return UploadPipelineError(status_code=422, code=code, stage=_STAGE, message=message)


def _reject_json_constant(value: str):
    raise ValueError(f"Unsupported JSON constant: {value}")


def parse_document_metadata(raw: str | None) -> dict[str, str | int | float | bool]:
    """Parse and validate the optional upload `metadata` field.

    v1 accepts a flat JSON object whose values are strings, numbers, or booleans.
    Omitted metadata yields ``{}``. Anything else raises ``UploadPipelineError`` (422).
    """
    if raw is None:
        return {}

    if len(raw.encode("utf-8")) > config.DOCUMENT_METADATA_MAX_BYTES:
        raise _invalid(
            "metadata_too_large",
            f"metadata exceeds the maximum size of {config.DOCUMENT_METADATA_MAX_BYTES} bytes.",
        )

    try:
        parsed = json.loads(raw, parse_constant=_reject_json_constant)
    except (ValueError, RecursionError):
        raise _invalid("invalid_metadata", "metadata must be valid JSON.") from None

    if not isinstance(parsed, dict):
        raise _invalid("invalid_metadata", "metadata must be a JSON object.")

    if len(parsed) > config.DOCUMENT_METADATA_MAX_KEYS:
        raise _invalid(
            "metadata_too_many_keys",
            f"metadata may contain at most {config.DOCUMENT_METADATA_MAX_KEYS} keys.",
        )

    for key, value in parsed.items():
        if not key or "\x00" in key:
            raise _invalid(
                "invalid_metadata",
                "metadata keys must be non-empty strings without NUL characters.",
            )
        if isinstance(value, str):
            if "\x00" in value:
                raise _invalid(
                    "invalid_metadata",
                    f"metadata value for '{key}' must not contain NUL characters.",
                )
        elif isinstance(value, bool):
            pass
        elif isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise _invalid(
                    "invalid_metadata",
                    f"metadata value for '{key}' must be a finite number.",
                )
        else:
            raise _invalid(
                "invalid_metadata",
                f"metadata value for '{key}' must be a string, number, or boolean.",
            )

    return parsed

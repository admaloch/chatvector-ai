from fastapi import Depends  # noqa: F401  — kept for Phase 3 wiring


def require_auth() -> dict:
    """
    Phase 3 placeholder.

    Will return tenant context once authentication is implemented.
    """
    return {"tenant_id": None}

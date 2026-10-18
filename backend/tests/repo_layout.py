"""Locate repository files from CI checkouts and the Docker tests container.

CI runs pytest with this tree at ``<repo>/backend/tests``. ``docker compose run
tests`` mounts only ``./backend`` at ``/app``, so ``parents[2]`` is ``/``.
Repo-root docs are bind-mounted at ``/workspace`` for that service.
"""

from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _TESTS_DIR.parent
_CHECKOUT_ROOT = _BACKEND_DIR.parent


def repo_file(*parts: str) -> Path:
    """Return the first existing path for a repo-relative file."""
    relative = Path(*parts)
    candidates = [
        _CHECKOUT_ROOT / relative,
        Path("/workspace") / relative,
    ]
    if relative.parts and relative.parts[0] == "backend":
        candidates.append(_BACKEND_DIR.joinpath(*relative.parts[1:]))
    for path in candidates:
        if path.is_file():
            return path
    checked = ", ".join(str(path) for path in candidates)
    raise AssertionError(f"Could not find {relative.as_posix()} (checked {checked})")

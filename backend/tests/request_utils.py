"""HTTP helpers for unit tests."""

from starlette.requests import Request


def make_test_request(method: str = "GET", path: str = "/") -> Request:
    """Minimal ASGI scope for Starlette Request (required by slowapi-decorated routes)."""
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
    )

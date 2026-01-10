import logging
from backend.middleware.request_id import get_request_id

class RequestIDFilter(logging.Filter):
    """
    Injects a formatted request_id_part into log records.

    - Uses the active request ID when available
    - Falls back to 'system' for startup/background logs
    """

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = get_request_id() or "system"
        record.request_id_part = f"[request_id={request_id}]"
        return True

class RequestIDFormatter(logging.Formatter):
    """
    Formatter that optionally hides system-level request IDs.
    Replaces %(request_id_part)s with [request_id=...] or empty string.
    """
    def __init__(self, fmt=None, datefmt=None, hide_system=False):
        super().__init__(fmt, datefmt)
        self.hide_system = hide_system

    def format(self, record: logging.LogRecord) -> str:
        if hasattr(record, "request_id") and record.request_id:
            record.request_id_part = f"[request_id={record.request_id}]"
        else:
            record.request_id_part = "" if self.hide_system else "[request_id=unknown]"
        return super().format(record) 

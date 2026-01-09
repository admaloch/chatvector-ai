import uuid
import contextvars
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

# Context variable to store request ID per request
request_id_var = contextvars.ContextVar("request_id", default=None)

def get_request_id() -> str:
    """
    Retrieve the current request ID from contextvar.
    Returns None if not set.
    """
    return request_id_var.get()

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        """
        Attach a request-scoped correlation ID to each incoming HTTP request.

        This middleware ensures every request has a unique X-Request-ID that is:
        - Generated or propagated from incoming headers
        - Stored using contextvars for async-safe, per-request isolation
        - Automatically cleaned up after the request completes to prevent cross-request leakage
        - Added to the response headers for end-to-end traceability
        """
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # Set context var and keep token for reset
        token = request_id_var.set(request_id)

        try:
            response = await call_next(request)
        finally:
            # Reset context to avoid leakage
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = request_id
        return response
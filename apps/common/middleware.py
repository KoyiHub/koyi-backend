"""Request correlation IDs.

Every request gets an ID (reusing an upstream `X-Request-ID` when present).
It is attached to the request, echoed in the response header, and injected into
every log record emitted while handling that request — so a log line can be
traced back to the request that produced it.
"""

import uuid
from collections.abc import Callable
from contextvars import ContextVar

from django.http import HttpRequest, HttpResponse

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

HEADER = "X-Request-ID"


def get_request_id() -> str:
    return _request_id.get()


class RequestIDMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.headers.get(HEADER) or uuid.uuid4().hex
        # Never trust an unbounded upstream value in logs/headers.
        request_id = request_id[:64]

        token = _request_id.set(request_id)
        request.request_id = request_id  # type: ignore[attr-defined]
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)

        response[HEADER] = request_id
        return response

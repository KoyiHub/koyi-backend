"""A single, predictable error envelope for every API response.

Every 4xx/5xx from DRF comes back as:

    {
      "error": {
        "type": "validation_error",
        "message": "Request could not be processed.",
        "detail": {...original DRF payload...},
        "request_id": "..."
      }
    }
"""

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.common.middleware import get_request_id


class ApplicationError(Exception):
    """Base class for domain errors that should surface as a clean 4xx.

    Raise these from services/selectors; the handler below turns them into a
    response so views don't have to catch-and-translate.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    default_message: str = "Something went wrong."
    error_type: str = "application_error"

    def __init__(self, message: str | None = None, detail=None) -> None:
        self.message = message or self.default_message
        self.detail = detail
        super().__init__(self.message)


class ConflictError(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_message = "The request conflicts with the current state."
    error_type = "conflict"


def _error_type(exc: Exception) -> str:
    if isinstance(exc, ApplicationError):
        return exc.error_type
    mapping = {
        exceptions.ValidationError: "validation_error",
        exceptions.NotAuthenticated: "not_authenticated",
        exceptions.AuthenticationFailed: "authentication_failed",
        exceptions.PermissionDenied: "permission_denied",
        exceptions.NotFound: "not_found",
        exceptions.MethodNotAllowed: "method_not_allowed",
        exceptions.Throttled: "throttled",
    }
    for exc_class, name in mapping.items():
        if isinstance(exc, exc_class):
            return name
    return "error"


def api_exception_handler(exc: Exception, context: dict) -> Response | None:
    """DRF EXCEPTION_HANDLER: normalise everything into one envelope."""
    # Translate non-DRF exceptions DRF wouldn't otherwise handle.
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(getattr(exc, "message_dict", exc.messages))
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()
    elif isinstance(exc, ApplicationError):
        return Response(
            {
                "error": {
                    "type": exc.error_type,
                    "message": exc.message,
                    "detail": exc.detail,
                    "request_id": get_request_id(),
                }
            },
            status=exc.status_code,
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled — let Django's 500 machinery log and report it.
        return None

    detail = response.data
    message = "Request could not be processed."
    if isinstance(detail, dict) and "detail" in detail:
        message = str(detail["detail"])
        detail = None
    elif isinstance(detail, list) and len(detail) == 1:
        message = str(detail[0])
        detail = None

    response.data = {
        "error": {
            "type": _error_type(exc),
            "message": message,
            "detail": detail,
            "request_id": get_request_id(),
        }
    }
    return response

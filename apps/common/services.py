"""Base for the service layer.

Services own business rules and are the only place a view is allowed to call
into. They compose repositories, enforce invariants and raise
`ApplicationError` subclasses; they never touch `request` or DRF types.
"""

from rest_framework import status

from apps.common.exceptions import ApplicationError


class NotFoundError(ApplicationError):
    status_code = status.HTTP_404_NOT_FOUND
    default_message = "The requested resource does not exist."
    error_type = "not_found"


class PermissionDeniedError(ApplicationError):
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "You do not have permission to perform this action."
    error_type = "permission_denied"


class ValidationError(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "The request is not valid."
    error_type = "validation_error"


class AuthenticationError(ApplicationError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_message = "Authentication failed."
    error_type = "authentication_failed"


class ThrottleError(ApplicationError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_message = "Too many requests. Please wait before retrying."
    error_type = "throttled"


class BaseService:
    """Marker base so services are greppable and share a construction style."""

    def __init__(self, **context) -> None:
        # Portal services are handed their acting principal (school/teacher/
        # student) at construction, so every query below is already scoped.
        for key, value in context.items():
            setattr(self, key, value)

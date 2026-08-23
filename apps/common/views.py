"""Operational probes for load balancers and orchestrators."""

from django.db import connections, transaction
from django.db.utils import OperationalError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

# ATOMIC_REQUESTS wraps every view in a transaction. Probes are hit constantly
# by the load balancer, so they opt out: liveness must not need a database at
# all, and readiness should not hold a transaction open just to run SELECT 1.


@transaction.non_atomic_requests
@require_GET
@never_cache
def health_check(request: HttpRequest) -> JsonResponse:  # noqa: ARG001
    """Liveness: is the process up? Deliberately touches nothing external."""
    return JsonResponse({"status": "ok"})


@transaction.non_atomic_requests
@require_GET
@never_cache
def readiness_check(request: HttpRequest) -> JsonResponse:  # noqa: ARG001
    """Readiness: can this process actually serve traffic?"""
    checks: dict[str, str] = {}
    healthy = True

    try:
        connections["default"].cursor().execute("SELECT 1")
        checks["database"] = "ok"
    except OperationalError as exc:
        checks["database"] = f"error: {exc}"
        healthy = False

    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )

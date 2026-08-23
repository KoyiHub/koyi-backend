from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.common.views import health_check, readiness_check

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    # Ops probes — unauthenticated by design, no DB write.
    path("healthz/", health_check, name="health-check"),
    path("readyz/", readiness_check, name="readiness-check"),
    # API
    path("api/v1/", include(("config.api_urls", "api"), namespace="v1")),
    # Schema + docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    if "debug_toolbar" in settings.INSTALLED_APPS:
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]

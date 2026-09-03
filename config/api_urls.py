"""Version 1 of the public API. Mounted at /api/v1/ by config.urls."""

from django.urls import include, path

urlpatterns = [
    path("auth/", include("apps.users.urls")),
    path("school/", include("apps.school_portal.urls")),
]

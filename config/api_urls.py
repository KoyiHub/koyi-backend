"""Version 1 of the public API. Mounted at /api/v1/ by config.urls."""

from django.urls import include, path

from apps.school_portal import views

urlpatterns = [
    path("auth/", include("apps.users.urls")),
    path("school/", include("apps.school_portal.urls")),
    path("teacher/", include("apps.teacher_portal.urls")),
    path("student/", include("apps.student_portal.urls")),
]

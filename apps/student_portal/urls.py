"""Routes for the assessment runner, mounted at /api/v1/student/."""

from django.urls import path

from apps.student_portal import views

app_name = "student_portal"

urlpatterns = [
    path("assessment/verify/", views.VerifyView.as_view(), name="verify"),
    path("assessment/", views.OverviewView.as_view(), name="overview"),
    path(
        "assessment/sections/<uuid:section_id>/start/",
        views.StartSectionView.as_view(),
        name="section-start",
    ),
    path(
        "assessment/sections/<uuid:section_id>/submit/",
        views.SubmitSectionView.as_view(),
        name="section-submit",
    ),
    path(
        "assessment/responses/<uuid:question_id>/",
        views.SaveResponseView.as_view(),
        name="save-response",
    ),
]

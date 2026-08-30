"""Version 1 of the public API. Mounted at /api/v1/ by config.urls."""

from django.urls import include, path

from apps.school_portal import views

urlpatterns = [
    path("auth/", include("apps.users.urls")),
    path("auth/teacher/login/", views.TeacherLoginView.as_view()),
    path("auth/school-admin/login/", views.SchoolLoginView.as_view()),
    # Registration + the school's own profile
    path("schools/register/", views.SchoolRegisterView.as_view()),
    path("schools/verify-email/", views.SchoolVerifyEmailView.as_view()),
    path("schools/verify-email/resend/", views.SchoolResendVerificationView.as_view()),
    path("schools/me/", views.SchoolProfileView.as_view()),
    path("schools/me/overview/", views.SchoolOverviewView.as_view()),
    # Reference data
    path("sessions/", views.SessionListView.as_view()),
    path("grades/", views.GradeListView.as_view()),
    path("classes/", views.SchoolClassListView.as_view()),
    # Teachers
    path("teachers/", views.TeacherListCreateView.as_view()),
    path("teachers/<uuid:pk>/", views.TeacherDetailView.as_view()),
    # Students
    path("students/", views.StudentListCreateView.as_view()),
    path("students/<uuid:pk>/", views.StudentDetailView.as_view()),
    # Assessment oversight
    path("assessments/oversight/", views.AssessmentOversightListView.as_view()),
]

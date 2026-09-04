"""Routes for the school management dashboard, mounted at /api/v1/school/."""

from django.urls import path

from apps.school_portal import views

app_name = "school_portal"

urlpatterns = [
    # Auth. Registration and sign-in are both two-step: credentials, then a
    # code sent to the address on file.
    path("auth/register/", views.SchoolRegisterView.as_view(), name="register"),
    path(
        "auth/register/verify/",
        views.SchoolRegisterVerifyView.as_view(),
        name="register-verify",
    ),
    path("auth/otp/resend/", views.SchoolResendCodeView.as_view(), name="otp-resend"),
    path("auth/login/", views.SchoolLoginView.as_view(), name="login"),
    path("auth/login/verify/", views.SchoolLoginVerifyView.as_view(), name="login-verify"),
    path(
        "auth/password/reset/request/",
        views.PasswordResetRequestView.as_view(),
        name="password-reset-request",
    ),
    path(
        "auth/password/reset/verify/",
        views.PasswordResetVerifyView.as_view(),
        name="password-reset-verify",
    ),
    path(
        "auth/password/reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    # Profile
    path("profile/", views.SchoolProfileView.as_view(), name="profile"),
    path(
        "profile/password/change/",
        views.SchoolPasswordChangeView.as_view(),
        name="password-change",
    ),
    # Reference data
    path("grades/", views.GradeListView.as_view(), name="grade-list"),
    path("classes/", views.SchoolClassListCreateView.as_view(), name="class-list"),
    path("classes/<uuid:pk>/", views.SchoolClassDetailView.as_view(), name="class-detail"),
    path("sessions/", views.SessionListView.as_view(), name="session-list"),
    # Teachers
    path("teachers/", views.TeacherListCreateView.as_view(), name="teacher-list"),
    path("teachers/<uuid:pk>/", views.TeacherDetailView.as_view(), name="teacher-detail"),
    path(
        "teachers/<uuid:pk>/disable/",
        views.TeacherActiveView.as_view(active=False),
        name="teacher-disable",
    ),
    path(
        "teachers/<uuid:pk>/enable/",
        views.TeacherActiveView.as_view(active=True),
        name="teacher-enable",
    ),
    path(
        "teachers/<uuid:pk>/password-reset/",
        views.TeacherPasswordResetView.as_view(),
        name="teacher-password-reset",
    ),
    path(
        "teachers/<uuid:pk>/delete/request/",
        views.TeacherDeleteRequestView.as_view(),
        name="teacher-delete-request",
    ),
    path(
        "teachers/<uuid:pk>/delete/confirm/",
        views.TeacherDeleteConfirmView.as_view(),
        name="teacher-delete-confirm",
    ),
    # Students. The two transfer routes come before `<uuid:pk>/` so that
    # "transfer" is never read as a student id.
    path("students/", views.StudentListCreateView.as_view(), name="student-list"),
    path("students/transfer/", views.StudentTransferView.as_view(), name="student-transfer"),
    path(
        "students/transfer-class/",
        views.ClassTransferView.as_view(),
        name="student-transfer-class",
    ),
    path("students/<uuid:pk>/", views.StudentDetailView.as_view(), name="student-detail"),
    path("students/<uuid:pk>/fln/", views.StudentFLNView.as_view(), name="student-fln"),
    path(
        "students/<uuid:pk>/disable/",
        views.StudentActiveView.as_view(active=False),
        name="student-disable",
    ),
    path(
        "students/<uuid:pk>/enable/",
        views.StudentActiveView.as_view(active=True),
        name="student-enable",
    ),
    path(
        "students/<uuid:pk>/delete/request/",
        views.StudentDeleteRequestView.as_view(),
        name="student-delete-request",
    ),
    path(
        "students/<uuid:pk>/delete/confirm/",
        views.StudentDeleteConfirmView.as_view(),
        name="student-delete-confirm",
    ),
    # Activity
    path("activity/", views.ActivityFeedView.as_view(), name="activity"),
    # Oversight
    path("assessments/", views.AssessmentOversightListView.as_view(), name="assessment-list"),
    path("overview/", views.SchoolOverviewView.as_view(), name="overview"),
]

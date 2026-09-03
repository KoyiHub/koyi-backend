"""Routes for the school management dashboard, mounted at /api/v1/school/."""

from django.urls import path

from apps.school_portal import views

app_name = "school_portal"

urlpatterns = [
    # Auth
    path("auth/register/", views.SchoolRegisterView.as_view(), name="register"),
    path("auth/login/", views.SchoolLoginView.as_view(), name="login"),
    # Profile
    path("profile/", views.SchoolProfileView.as_view(), name="profile"),
    # Reference data
    path("grades/", views.GradeListView.as_view(), name="grade-list"),
    path("classes/", views.SchoolClassListView.as_view(), name="class-list"),
    path("sessions/", views.SessionListView.as_view(), name="session-list"),
    # Teachers
    path("teachers/", views.TeacherListCreateView.as_view(), name="teacher-list"),
    path("teachers/<uuid:pk>/", views.TeacherDetailView.as_view(), name="teacher-detail"),
    # Students
    path("students/", views.StudentListCreateView.as_view(), name="student-list"),
    path("students/<uuid:pk>/", views.StudentDetailView.as_view(), name="student-detail"),
    # Oversight
    path("assessments/", views.AssessmentOversightListView.as_view(), name="assessment-list"),
    path("overview/", views.SchoolOverviewView.as_view(), name="overview"),
]

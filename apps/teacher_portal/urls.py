"""Routes for the teacher dashboard, mounted at /api/v1/teacher/."""

from django.urls import path

from apps.teacher_portal import views

app_name = "teacher_portal"

urlpatterns = [
    # Auth
    path("auth/login/", views.TeacherLoginView.as_view(), name="login"),
    path("auth/me/", views.TeacherMeView.as_view(), name="me"),
    # Taxonomy and bank — read-only
    path("bank/skills/", views.SkillListView.as_view(), name="skill-list"),
    path("bank/questions/", views.BankQuestionListView.as_view(), name="bank-question-list"),
    path(
        "bank/questions/<uuid:pk>/",
        views.BankQuestionDetailView.as_view(),
        name="bank-question-detail",
    ),
    # Assessments
    path("assessments/", views.AssessmentListCreateView.as_view(), name="assessment-list"),
    path("assessments/<uuid:pk>/", views.AssessmentDetailView.as_view(), name="assessment-detail"),
    path(
        "assessments/<uuid:pk>/sections/",
        views.SectionListCreateView.as_view(),
        name="section-list",
    ),
    path(
        "assessments/<uuid:pk>/sections/<uuid:section_id>/",
        views.SectionDetailView.as_view(),
        name="section-detail",
    ),
    path(
        "assessments/<uuid:pk>/sections/<uuid:section_id>/questions/",
        views.SectionQuestionsView.as_view(),
        name="section-questions",
    ),
    path(
        "assessments/<uuid:pk>/coverage/",
        views.AssessmentCoverageView.as_view(),
        name="assessment-coverage",
    ),
    path(
        "assessments/<uuid:pk>/assignments/",
        views.AssignmentListCreateView.as_view(),
        name="assignment-list",
    ),
    path(
        "assessments/<uuid:pk>/assignments/<uuid:assignment_id>/",
        views.AssignmentDetailView.as_view(),
        name="assignment-detail",
    ),
    path(
        "assessments/<uuid:pk>/assignments/roster/",
        views.AssignmentRosterView.as_view(),
        name="assignment-roster",
    ),
    path(
        "assessments/<uuid:pk>/analytics/",
        views.AssessmentAnalyticsView.as_view(),
        name="assessment-analytics",
    ),
    path(
        "assessments/<uuid:pk>/analytics/roster/",
        views.AssessmentRosterView.as_view(),
        name="assessment-roster",
    ),
    path(
        "assessments/<uuid:pk>/results/<uuid:student_id>/responses/",
        views.ResponseReviewView.as_view(),
        name="response-review",
    ),
    path(
        "students/<uuid:pk>/skills/",
        views.StudentBreakdownView.as_view(),
        name="student-skills",
    ),
    path(
        "assessments/<uuid:pk>/publish/",
        views.AssessmentPublishView.as_view(),
        name="assessment-publish",
    ),
]

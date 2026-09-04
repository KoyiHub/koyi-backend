"""What a paper says about a class, and about one child."""

import pytest
from django.urls import reverse

from apps.ai.client import ScriptedClient, register_client, reset_client
from apps.ai.enums import JobType
from apps.assessments.enums import AssessmentStatus, ResultStatus
from apps.assessments.models import (
    Assessment,
    AssessmentQuestion,
    AssessmentQuestionAnswer,
    AssessmentQuestionResponse,
    AssessmentResult,
    AssessmentSection,
    PlacementRule,
)
from apps.assessments.placement import DiagnosisService
from apps.common.enums import Domain, QuestionType
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.schools.factories import SchoolClassFactory, StudentFactory, TeacherFactory

pytestmark = pytest.mark.django_db

ANALYTICS = "v1:teacher_portal:assessment-analytics"
ROSTER = "v1:teacher_portal:assessment-roster"
SKILLS = "v1:teacher_portal:student-skills"


@pytest.fixture(autouse=True)
def _isolate_ai():
    reset_client()
    yield
    reset_client()


@pytest.fixture
def teacher():
    return TeacherFactory()


@pytest.fixture
def client(api_client, teacher):
    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken.for_user(teacher.user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api_client


@pytest.fixture
def cohort(teacher):
    """Three children, placed at three different levels."""
    skill = SkillFactory(domain=Domain.LITERACY, code="an_skill", min_level=1, max_level=3)
    subskill = SubskillFactory(skill=skill, code="an_sub", name="Letter sounds")
    for level in (1, 2, 3):
        PlacementRule.objects.update_or_create(
            domain=Domain.LITERACY,
            fln_level=level,
            defaults={"required_skills": 1, "applicable_skills": 1},
        )

    assessment = Assessment.objects.create(
        school=teacher.school,
        teacher=teacher,
        name="Baseline",
        status=AssessmentStatus.PUBLISHED,
        code="ANALYT",
    )
    section = AssessmentSection.objects.create(
        assessment=assessment, domain=Domain.LITERACY, name="Reading", order=1
    )
    school_class = SchoolClassFactory(school=teacher.school)

    questions = {}
    for level in (1, 2, 3):
        question = AssessmentQuestion.objects.create(
            section=section,
            assessment=assessment,
            subskill=subskill,
            skill=skill,
            fln_level=level,
            text=f"Level {level} question",
            question_type=QuestionType.TEXT,
            order=level,
            point=1,
        )
        AssessmentQuestionAnswer.objects.create(assessment_question=question, value="a")
        questions[level] = question

    students = []
    # Each child clears one more level than the last.
    for ceiling in (0, 1, 2):
        student = StudentFactory(school=teacher.school, school_class=school_class)
        assessment.assignments.create(
            student=student, code=f"CODE{ceiling}", status=ResultStatus.FINISHED
        )
        AssessmentResult.objects.create(assessment=assessment, student=student)
        for level in (1, 2, 3):
            AssessmentQuestionResponse.objects.create(
                assessment_question=questions[level],
                student=student,
                assessment=assessment,
                type=QuestionType.TEXT,
                text_value="a",
                is_correct=level <= ceiling,
            )
        DiagnosisService().run(assessment, student)
        students.append(student)
    return assessment, students


class TestAnalytics:
    def test_it_leads_with_level_distribution(self, client, cohort):
        assessment, _ = cohort
        response = client.get(reverse(ANALYTICS, args=[assessment.pk]), {"narrative": "false"})

        assert response.status_code == 200
        literacy = response.data["level_distribution"]["literacy"]
        # One child needs level 1, one level 2, one level 3.
        assert literacy == {"1": 1, "2": 1, "3": 1, "4": 0, "5": 0}

    def test_every_level_is_keyed_even_at_zero(self, client, cohort):
        """A chart that omits empty levels reads as a narrower spread."""
        assessment, _ = cohort
        response = client.get(reverse(ANALYTICS, args=[assessment.pk]), {"narrative": "false"})
        for domain in ("literacy", "numeracy"):
            assert sorted(response.data["level_distribution"][domain]) == ["1", "2", "3", "4", "5"]

    def test_marking_status_is_always_present(self, client, cohort):
        assessment, _ = cohort
        response = client.get(reverse(ANALYTICS, args=[assessment.pk]), {"narrative": "false"})
        status = response.data["marking_status"]
        assert status["total"] == 9
        assert status["complete"] is True

    def test_pending_marking_produces_a_warning(self, client, cohort, teacher):
        assessment, students = cohort
        AssessmentQuestionResponse.objects.filter(student=students[0]).update(is_correct=None)

        response = client.get(reverse(ANALYTICS, args=[assessment.pk]), {"narrative": "false"})
        assert response.data["marking_status"]["pending"] == 3
        assert any("still being marked" in w for w in response.data["warnings"])

    def test_most_missed_is_by_subskill_and_level(self, client, cohort):
        """Not by question: a subskill at a level is what a teacher can teach."""
        assessment, _ = cohort
        response = client.get(reverse(ANALYTICS, args=[assessment.pk]), {"narrative": "false"})

        missed = response.data["most_missed"]
        assert missed
        assert {"subskill_name", "fln_level", "failed_pct"} <= set(missed[0])
        # Level 3 was failed by everyone, level 1 by one child.
        assert missed[0]["fln_level"] == 3

    def test_the_skill_matrix_carries_level_context(self, client, cohort):
        assessment, _ = cohort
        response = client.get(reverse(ANALYTICS, args=[assessment.pk]), {"narrative": "false"})
        cells = response.data["skill_matrix"]
        assert {cell["fln_level"] for cell in cells} == {1, 2, 3}

    def test_participation_counts_submissions(self, client, cohort):
        assessment, _ = cohort
        response = client.get(reverse(ANALYTICS, args=[assessment.pk]), {"narrative": "false"})
        assert response.data["participation"] == {"assigned": 3, "started": 3, "submitted": 3}


class TestNarrative:
    def test_it_is_laid_over_the_numbers(self, client, cohort):
        assessment, _ = cohort
        register_client(
            ScriptedClient(
                replies={
                    JobType.ASSESSMENT_ANALYTICS: {
                        "summary": "Most of the class is working at Level 2.",
                        "attention": "Letter sounds",
                        "strength": "",
                    }
                }
            )
        )
        response = client.get(reverse(ANALYTICS, args=[assessment.pk]))
        assert response.data["narrative"]["attention"] == "Letter sounds"

    def test_a_dead_model_does_not_take_the_numbers_with_it(self, client, cohort):
        """The figures are the diagnosis; the prose is a convenience."""
        assessment, _ = cohort
        register_client(ScriptedClient(replies={}))

        response = client.get(reverse(ANALYTICS, args=[assessment.pk]))
        assert response.status_code == 200
        assert response.data["narrative"] is None
        assert response.data["level_distribution"]["literacy"]["1"] == 1

    def test_it_can_be_skipped_without_changing_the_shape(self, client, cohort):
        """The key stays, as null.

        A dashboard tile that does not want the prose should not have to branch
        on whether the key exists - null and absent read the same to a person
        and differently to a parser.
        """
        assessment, _ = cohort
        register_client(ScriptedClient(replies={}))
        response = client.get(reverse(ANALYTICS, args=[assessment.pk]), {"narrative": "false"})
        assert response.data["narrative"] is None


class TestRoster:
    def test_it_names_children_and_their_weak_subskills(self, client, cohort):
        assessment, _ = cohort
        response = client.get(reverse(ROSTER, args=[assessment.pk]))

        assert response.status_code == 200
        assert len(response.data) == 3
        assert all(row["full_name"] for row in response.data)
        assert any(row["weak_subskills"] for row in response.data)

    def test_it_filters_by_level(self, client, cohort):
        assessment, _ = cohort
        response = client.get(
            reverse(ROSTER, args=[assessment.pk]), {"domain": "literacy", "level": 1}
        )
        assert len(response.data) == 1
        assert response.data[0]["literacy_level"] == 1


class TestStudentBreakdown:
    def test_it_shows_where_a_skill_broke_down(self, client, cohort):
        _, students = cohort
        response = client.get(reverse(SKILLS, args=[students[2].pk]), {"narrative": "false"})

        assert response.status_code == 200
        skill = response.data["skills"][0]
        # Cleared levels 1 and 2, failed 3.
        assert skill["highest_level_passed"] == 2
        assert skill["broke_down_at"] == 3
        assert skill["weak_subskills"]

    def test_the_two_levels_are_reported_separately(self, client, cohort):
        _, students = cohort
        response = client.get(reverse(SKILLS, args=[students[1].pk]), {"narrative": "false"})
        assert response.data["literacy_level"] == 2
        assert response.data["numeracy_level"] is None

    def test_another_school_s_child_is_not_visible(self, client, cohort):
        outsider = StudentFactory()
        response = client.get(reverse(SKILLS, args=[outsider.pk]))
        assert response.status_code == 404

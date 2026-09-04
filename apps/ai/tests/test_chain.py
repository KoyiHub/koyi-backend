"""The second marking pass, and what it does to a diagnosis.

Objective items are settled immediately and a child is placed on those. Written
and spoken answers arrive later, so placement has to be able to run twice and
land somewhere better the second time — without the first run having been wrong.
"""

import pytest

from apps.ai.client import ScriptedClient, register_client, reset_client
from apps.ai.enums import JobType
from apps.ai.tasks import mark_free_form_responses_task
from apps.ai.transcription import reset_transcriber
from apps.assessments.enums import AssessmentStatus
from apps.assessments.models import (
    Assessment,
    AssessmentQuestion,
    AssessmentQuestionAnswer,
    AssessmentQuestionResponse,
    AssessmentResult,
    AssessmentSection,
    PlacementRule,
    SkillLevelResult,
)
from apps.common.enums import Domain, QuestionType
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.schools.factories import SchoolFactory, StudentFactory, TeacherFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolate_ai():
    reset_client()
    reset_transcriber()
    yield
    reset_client()
    reset_transcriber()


@pytest.fixture
def written_paper():
    skill = SkillFactory(domain=Domain.LITERACY, code="chain_skill", min_level=1, max_level=2)
    subskill = SubskillFactory(skill=skill, code="chain_sub")
    PlacementRule.objects.update_or_create(
        domain=Domain.LITERACY,
        fln_level=1,
        defaults={"required_skills": 1, "applicable_skills": 1},
    )

    school = SchoolFactory()
    student = StudentFactory(school=school)
    assessment = Assessment.objects.create(
        school=school,
        teacher=TeacherFactory(school=school),
        name="Written",
        status=AssessmentStatus.PUBLISHED,
        code="CHAINA",
    )
    AssessmentResult.objects.create(assessment=assessment, student=student)
    section = AssessmentSection.objects.create(
        assessment=assessment, domain=Domain.LITERACY, name="Writing", order=1
    )
    responses = []
    for index in range(2):
        question = AssessmentQuestion.objects.create(
            section=section,
            assessment=assessment,
            subskill=subskill,
            skill=skill,
            fln_level=1,
            text=f"Write the word {index}",
            question_type=QuestionType.TEXT,
            order=index + 1,
            point=1,
        )
        AssessmentQuestionAnswer.objects.create(assessment_question=question, value="cat")
        responses.append(
            AssessmentQuestionResponse.objects.create(
                assessment_question=question,
                student=student,
                assessment=assessment,
                type=QuestionType.TEXT,
                text_value="cat",
            )
        )
    return assessment, student, responses


class TestSecondPass:
    def test_written_answers_are_marked_and_the_child_is_placed(self, written_paper):
        assessment, student, _ = written_paper
        register_client(
            ScriptedClient(
                replies={
                    JobType.MARK_TEXT_RESPONSE: {
                        "is_correct": True,
                        "confidence": 0.9,
                        "error_type": "",
                        "observation_note": "Correct.",
                    }
                }
            )
        )

        result = mark_free_form_responses_task(str(assessment.pk), str(student.pk))

        assert result["marked"] == 2
        # Marking alone is not the point — the diagnosis has to follow.
        assert SkillLevelResult.objects.filter(student=student).exists()

    def test_low_confidence_answers_are_flagged_not_counted(self, written_paper):
        assessment, student, _ = written_paper
        register_client(
            ScriptedClient(
                replies={
                    JobType.MARK_TEXT_RESPONSE: {
                        "is_correct": False,
                        "confidence": 0.1,
                        "error_type": "no_response",
                        "observation_note": "Illegible.",
                    }
                }
            )
        )

        result = mark_free_form_responses_task(str(assessment.pk), str(student.pk))

        assert result["flagged"] == 2
        assert result["marked"] == 0
        # Nothing was settled, so there is nothing to diagnose from.
        assert not SkillLevelResult.objects.filter(student=student).exists()

    def test_a_dead_provider_leaves_every_answer_pending(self, written_paper):
        assessment, student, responses = written_paper
        register_client(ScriptedClient(replies={}))

        result = mark_free_form_responses_task(str(assessment.pk), str(student.pk))

        assert result["skipped"] == 2
        for response in responses:
            response.refresh_from_db()
            assert response.is_correct is None

    def test_already_marked_answers_are_left_alone(self, written_paper):
        """The task only looks at what is pending, so a re-run is cheap."""
        assessment, student, responses = written_paper
        responses[0].is_correct = True
        responses[0].save(update_fields=["is_correct"])
        register_client(
            ScriptedClient(
                replies={
                    JobType.MARK_TEXT_RESPONSE: {
                        "is_correct": True,
                        "confidence": 0.9,
                        "error_type": "",
                        "observation_note": "ok",
                    }
                }
            )
        )

        result = mark_free_form_responses_task(str(assessment.pk), str(student.pk))
        assert result["marked"] == 1

    def test_uploads_have_no_marker_and_are_left_for_a_teacher(self, written_paper):
        assessment, student, _ = written_paper
        section = AssessmentSection.objects.get(assessment=assessment)
        subskill = SkillFactory(
            domain=Domain.LITERACY, code="upload_skill", min_level=1, max_level=1
        )
        sub = SubskillFactory(skill=subskill, code="upload_sub")
        question = AssessmentQuestion.objects.create(
            section=section,
            assessment=assessment,
            subskill=sub,
            skill=subskill,
            fln_level=1,
            text="Upload your work",
            question_type=QuestionType.FILE_UPLOAD,
            order=9,
            point=1,
        )
        response = AssessmentQuestionResponse.objects.create(
            assessment_question=question,
            student=student,
            assessment=assessment,
            type=QuestionType.FILE_UPLOAD,
        )
        register_client(ScriptedClient(replies={}))

        mark_free_form_responses_task(str(assessment.pk), str(student.pk))
        response.refresh_from_db()
        assert response.is_correct is None

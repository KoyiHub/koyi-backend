"""What the AI layer does, and what it refuses to do.

The failure paths matter more than the happy one here. A model that is down,
slow, or confidently wrong must never turn into a wrong mark on a child's
record — so most of what follows is about what happens when the call does not
work.
"""

import pytest

from apps.ai import jobs, prompts
from apps.ai.client import (
    LLMError,
    PromptBundle,
    ScriptedClient,
    register_client,
    reset_client,
)
from apps.ai.enums import GenerationStatus, JobType
from apps.ai.models import AIGeneration, AIPromptDocument
from apps.ai.schemas import SchemaError, parse_marking, parse_tags
from apps.ai.transcription import (
    ScriptedTranscriber,
    register_transcriber,
    reset_transcriber,
)
from apps.assessments.enums import AssessmentStatus, ErrorType, GradedBy
from apps.assessments.models import (
    Assessment,
    AssessmentQuestion,
    AssessmentQuestionAnswer,
    AssessmentQuestionResponse,
    AssessmentSection,
)
from apps.common.enums import Domain, QuestionType
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.media_assets.models import MediaAsset
from apps.schools.factories import SchoolFactory, StudentFactory, TeacherFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolate_ai():
    """Clients and the prompt cache are process-global.

    Without this, one case's scripted replies answer the next one's calls and
    the failures show up somewhere unrelated.
    """
    reset_client()
    reset_transcriber()
    prompts.reset_cache()
    yield
    reset_client()
    reset_transcriber()
    prompts.reset_cache()


@pytest.fixture
def subskill():
    skill = SkillFactory(domain=Domain.LITERACY, code="ai_phonics", min_level=1, max_level=3)
    return SubskillFactory(skill=skill, code="ai_letter_sounds", name="Letter sounds")


@pytest.fixture
def written(subskill):
    school = SchoolFactory()
    student = StudentFactory(school=school)
    assessment = Assessment.objects.create(
        school=school,
        teacher=TeacherFactory(school=school),
        name="Paper",
        status=AssessmentStatus.PUBLISHED,
        code="AIJOBS",
    )
    section = AssessmentSection.objects.create(
        assessment=assessment, domain=Domain.LITERACY, name="Writing", order=1
    )
    question = AssessmentQuestion.objects.create(
        section=section,
        assessment=assessment,
        subskill=subskill,
        skill=subskill.skill,
        fln_level=1,
        text="What is this animal called?",
        question_type=QuestionType.TEXT,
        order=1,
        point=1,
    )
    AssessmentQuestionAnswer.objects.create(assessment_question=question, value="elephant")
    return AssessmentQuestionResponse.objects.create(
        assessment_question=question,
        student=student,
        assessment=assessment,
        type=QuestionType.TEXT,
        text_value="elefant",
    )


def script(**replies):
    client = ScriptedClient(replies=replies)
    register_client(client)
    return client


class TestMarking:
    def test_a_verdict_is_written_onto_the_response(self, written):
        script(
            **{
                JobType.MARK_TEXT_RESPONSE: {
                    "is_correct": True,
                    "confidence": 0.92,
                    "error_type": "",
                    "observation_note": "Spelled phonetically but recognised it.",
                }
            }
        )
        outcome = jobs.mark_written_response(written)
        jobs.apply_marking(written, outcome.value)

        written.refresh_from_db()
        assert written.is_correct is True
        assert written.graded_by == GradedBy.AI
        assert written.awarded_points == written.assessment_question.point
        assert "phonetically" in written.observation_note

    def test_a_low_confidence_verdict_is_stored_but_not_applied(self, written):
        """Pending is not wrong. A teacher decides rather than the model."""
        script(
            **{
                JobType.MARK_TEXT_RESPONSE: {
                    "is_correct": False,
                    "confidence": 0.2,
                    "error_type": "substitution",
                    "observation_note": "Hard to read.",
                }
            }
        )
        outcome = jobs.mark_written_response(written)
        jobs.apply_marking(written, outcome.value)

        written.refresh_from_db()
        assert written.is_correct is None
        assert float(written.grading_confidence) == pytest.approx(0.2)
        assert written.observation_note == "Hard to read."

    def test_a_provider_failure_leaves_the_response_untouched(self, written):
        register_client(ScriptedClient(replies={}))  # no reply for any job
        outcome = jobs.mark_written_response(written)

        assert outcome.value is None
        written.refresh_from_db()
        assert written.is_correct is None
        assert written.graded_by == ""

    def test_every_call_is_recorded_even_when_it_fails(self, written):
        register_client(ScriptedClient(replies={}))
        jobs.mark_written_response(written)

        record = AIGeneration.objects.get()
        assert record.status == GenerationStatus.FAILED
        assert record.job_type == JobType.MARK_TEXT_RESPONSE
        assert record.subject_id == str(written.pk)
        assert record.error

    def test_a_successful_call_records_what_produced_it(self, written):
        script(
            **{
                JobType.MARK_TEXT_RESPONSE: {
                    "is_correct": True,
                    "confidence": 0.9,
                    "error_type": "",
                    "observation_note": "ok",
                }
            }
        )
        jobs.mark_written_response(written)

        record = AIGeneration.objects.get()
        assert record.status == GenerationStatus.SUCCEEDED
        assert record.model_id == "scripted"
        assert record.prompt_version
        assert record.input_hash


class TestSpokenMarking:
    def test_a_transcript_is_stored_beside_the_audio(self, written):
        """So a teacher can see what the recogniser thought it heard."""
        script(
            **{
                JobType.MARK_AUDIO_RESPONSE: {
                    "is_correct": True,
                    "confidence": 0.85,
                    "error_type": "",
                    "observation_note": "Read it cleanly.",
                }
            }
        )
        outcome = jobs.mark_spoken_response(written, transcript="elephant")
        jobs.apply_marking(written, outcome.value, transcript="elephant")

        written.refresh_from_db()
        assert written.transcript == "elephant"
        assert written.is_correct is True

    def test_a_failed_transcription_marks_nothing(self, written):
        """A microphone failure is not a wrong answer."""
        register_transcriber(ScriptedTranscriber(text=""))
        written.media_value = MediaAsset.objects.create(
            type="audio",
            url="https://example.test/a.wav",
            mime_type="audio/wav",
            original_filename="a.wav",
            size_bytes=10,
        )
        written.save(update_fields=["media_value"])

        assert jobs.transcribe_response(written) == ""
        written.refresh_from_db()
        assert written.is_correct is None

    def test_a_response_with_no_audio_transcribes_to_nothing(self, written):
        assert jobs.transcribe_response(written) is None


class TestTagSuggestion:
    def test_a_valid_suggestion_comes_back(self, subskill):
        script(
            **{
                JobType.SUGGEST_QUESTION_TAGS: {
                    "subskill_code": subskill.code,
                    "fln_level": 2,
                    "confidence": 0.8,
                    "reasoning": "The child must map a sound to a letter.",
                }
            }
        )
        outcome = jobs.suggest_question_tags(
            text="Which letter makes this sound?", options=["B", "D"], subskills=[subskill]
        )
        assert outcome.value.subskill_code == subskill.code
        assert outcome.value.fln_level == 2

    def test_an_invented_subskill_is_rejected_and_recorded(self, subskill):
        script(
            **{
                JobType.SUGGEST_QUESTION_TAGS: {
                    "subskill_code": "not_a_real_subskill",
                    "fln_level": 1,
                    "confidence": 0.99,
                    "reasoning": "Confidently wrong.",
                }
            }
        )
        outcome = jobs.suggest_question_tags(text="Q", options=[], subskills=[subskill])

        assert outcome.value is None
        record = AIGeneration.objects.get()
        # Kept rather than dropped: a run of these is how drift announces itself.
        assert record.status == GenerationStatus.INVALID
        assert "unknown subskill" in record.error

    def test_a_level_outside_the_subskill_range_is_rejected(self, subskill):
        script(
            **{
                JobType.SUGGEST_QUESTION_TAGS: {
                    "subskill_code": subskill.code,
                    "fln_level": 5,
                    "confidence": 0.9,
                    "reasoning": "Out of range.",
                }
            }
        )
        outcome = jobs.suggest_question_tags(text="Q", options=[], subskills=[subskill])

        assert outcome.value is None
        assert "levels 1 to 3" in AIGeneration.objects.get().error


class TestParsers:
    def test_an_invented_error_type_becomes_other(self):
        verdict = parse_marking(
            {
                "is_correct": False,
                "confidence": 0.9,
                "error_type": "vibes",
                "observation_note": "n",
            }
        )
        # The note is worth keeping; the label is not, because grouping uses it.
        assert verdict.error_type == ErrorType.OTHER

    def test_a_correct_answer_cannot_carry_an_error_type(self):
        verdict = parse_marking(
            {
                "is_correct": True,
                "confidence": 0.9,
                "error_type": "substitution",
                "observation_note": "n",
            }
        )
        assert verdict.error_type == ""

    def test_confidence_out_of_range_is_clamped_not_rejected(self):
        assert (
            parse_marking(
                {
                    "is_correct": True,
                    "confidence": 1.4,
                    "error_type": "",
                    "observation_note": "",
                }
            ).confidence
            == 1.0
        )
        assert (
            parse_marking(
                {
                    "is_correct": True,
                    "confidence": "nonsense",
                    "error_type": "",
                    "observation_note": "",
                }
            ).confidence
            == 0.0
        )

    def test_a_non_boolean_verdict_is_refused(self):
        with pytest.raises(SchemaError):
            parse_marking(
                {"is_correct": "yes", "confidence": 1, "error_type": "", "observation_note": ""}
            )

    def test_tags_need_a_subskill_that_exists(self, subskill):
        with pytest.raises(SchemaError):
            parse_tags(
                {"subskill_code": "ghost", "fln_level": 1, "confidence": 1, "reasoning": ""},
                known_subskills={subskill.code: subskill},
            )


class TestPrompts:
    def test_seeded_documents_are_used_and_pinned(self, written):
        AIPromptDocument.objects.create(
            job_type=JobType.MARK_TEXT_RESPONSE,
            name="house-style",
            version="v1",
            content="Mark on the skill, not on spelling.",
        )
        prompts.reset_cache()

        bundle = prompts.build(JobType.MARK_TEXT_RESPONSE, payload="x")
        assert "not on spelling" in bundle.system
        assert bundle.prompt_version == "house-style@v1"

    def test_a_job_with_no_documents_falls_back_and_says_so(self):
        bundle = prompts.build(JobType.LESSON_PLAN_GROUP, payload="x")
        assert bundle.prompt_version == prompts.FALLBACK_VERSION
        assert "lesson_plan_group" in bundle.system

    def test_identical_inputs_fingerprint_alike(self):
        one = PromptBundle(system="a", user="b")
        two = PromptBundle(system="a", user="b")
        assert one.fingerprint() == two.fingerprint()
        assert PromptBundle(system="a", user="c").fingerprint() != one.fingerprint()


class TestScriptedClientIsRealEnough:
    def test_it_raises_the_same_error_a_provider_would(self):
        client = ScriptedClient(replies={})
        with pytest.raises(LLMError):
            client.complete(prompt=PromptBundle(system="s", user="u"), schema={}, job="anything")

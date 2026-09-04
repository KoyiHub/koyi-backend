"""Background AI work.

Tasks take ids and re-read through the same services the request path uses.
A failure here must never become a wrong mark: a response that could not be
marked stays pending for a teacher, which is why nothing below writes
`is_correct` on an error path.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=2,
)
def mark_free_form_responses_task(self, assessment_id: str, student_id: str) -> dict:  # noqa: ARG001
    """Mark everything the deterministic marker left pending, then re-diagnose.

    Runs after the objective pass, so a child is placed on what could be marked
    immediately and re-placed once the rest lands. That is why the diagnosis
    chain is idempotent - this is the second run, not a special path.
    """
    from apps.ai.jobs import (
        apply_marking,
        mark_spoken_response,
        mark_written_response,
        transcribe_response,
    )
    from apps.assessments.models import AssessmentQuestionResponse
    from apps.common.enums import QuestionType

    pending = AssessmentQuestionResponse.objects.filter(
        assessment_id=assessment_id, student_id=student_id, is_correct__isnull=True
    ).select_related(
        "assessment_question",
        "assessment_question__answer",
        "assessment_question__subskill",
        "media_value",
    )

    marked = flagged = skipped = 0
    for response in pending:
        question_type = response.assessment_question.question_type

        if question_type == QuestionType.AUDIO:
            transcript = transcribe_response(response)
            if transcript is None:
                # A microphone failure is not a wrong answer.
                skipped += 1
                continue
            outcome = mark_spoken_response(response, transcript=transcript)
            transcript_text = transcript
        elif question_type == QuestionType.TEXT:
            outcome = mark_written_response(response)
            transcript_text = ""
        else:
            # File uploads have no marker yet; a teacher handles them.
            skipped += 1
            continue

        verdict = outcome.value
        if verdict is None:
            # The call failed or came back unusable. The response stays
            # pending; a teacher sees it rather than the child being marked
            # on something that did not work.
            skipped += 1
            continue

        apply_marking(response, verdict, transcript=transcript_text)
        if verdict.needs_review:
            flagged += 1
        else:
            marked += 1

    if marked:
        from apps.assessments.tasks import diagnose_student_task

        diagnose_student_task.delay(str(assessment_id), str(student_id))

    logger.info(
        "free-form marking finished",
        extra={
            "assessment_id": assessment_id,
            "student_id": student_id,
            "marked": marked,
            "flagged": flagged,
            "skipped": skipped,
        },
    )
    return {"marked": marked, "flagged": flagged, "skipped": skipped}

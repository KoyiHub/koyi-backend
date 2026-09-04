"""Background work for the assessment loop.

Tasks take ids, never model instances, and re-read through the services the
request path uses. Every one is safe to run twice: the matrix is rebuilt rather
than appended to and placements are upserted, so a retry after a timeout
repeats work instead of corrupting a diagnosis.
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
    max_retries=3,
)
def diagnose_student_task(self, assessment_id: str, student_id: str) -> dict:  # noqa: ARG001
    """Mark, build the matrix, and place one child.

    Runs when a child submits their last section. Kept per child rather than
    per paper so one malformed response cannot stall the whole class, and so a
    child who finishes early is placed without waiting for the slowest.
    """
    from apps.assessments.models import Assessment
    from apps.assessments.placement import DiagnosisService
    from apps.schools.models import Student

    assessment = Assessment.objects.filter(pk=assessment_id).first()
    student = Student.objects.filter(pk=student_id).first()
    if assessment is None or student is None:
        # Deleted between enqueue and execution. Nothing to diagnose, and
        # retrying will not bring them back.
        logger.warning(
            "diagnosis skipped: missing row",
            extra={"assessment_id": assessment_id, "student_id": student_id},
        )
        return {"placed": 0}

    placements = DiagnosisService().run(assessment, student)
    logger.info(
        "student diagnosed",
        extra={
            "assessment_id": assessment_id,
            "student_id": student_id,
            "levels": {p.domain: p.level for p in placements},
        },
    )
    return {"placed": len(placements)}


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def diagnose_assessment_task(self, assessment_id: str) -> dict:  # noqa: ARG001
    """Re-diagnose everyone who finished a paper.

    The way to apply a changed placement threshold: the stored matrix is reused
    and only the rules are re-applied, so nothing is re-marked.
    """
    from apps.assessments.enums import ResultStatus
    from apps.assessments.models import AssessmentAssignment

    finished = AssessmentAssignment.objects.filter(
        assessment_id=assessment_id, status__in=[ResultStatus.FINISHED, ResultStatus.GRADED]
    ).values_list("student_id", flat=True)

    for student_id in finished:
        diagnose_student_task.delay(str(assessment_id), str(student_id))
    return {"queued": len(finished)}

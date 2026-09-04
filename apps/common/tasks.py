"""Shared Celery tasks, and the pattern new tasks should follow.

Tasks take primitives (IDs, not model instances) so payloads stay JSON-safe and
never carry stale state, and they set explicit retry policies.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def send_email_task(self, subject: str, body: str, recipients: list[str]) -> int:  # noqa: ARG001
    # `self` is unused here, but bind=True keeps it available for self.retry().
    """Send an email out-of-band. Returns the number of messages sent."""
    from django.core.mail import send_mail

    logger.info("sending email", extra={"subject": subject, "recipients": len(recipients)})
    return send_mail(subject=subject, message=body, from_email=None, recipient_list=recipients)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def purge_deleted_rows_task(self) -> dict:  # noqa: ARG001
    """Destroy what was soft-deleted longer ago than the retention window.

    A deletion in the dashboard hides the row; this is what eventually makes it
    true. The window exists because the two things a school needs from a delete
    button are in tension: it has to take effect immediately, and it has to be
    survivable when someone clicks it by mistake. Hiding now and destroying
    later gives both.

    Order matters. Students go first, because their results, responses and
    placements cascade off them and there is no point walking those rows twice.
    Teachers follow: their assessments hold a nullable author, so removing the
    teacher clears the byline rather than deleting the paper - which is the
    behaviour a school expects when a colleague leaves.

    Idempotent, like every task here: a row already gone is simply not found on
    the next run.
    """
    from django.utils import timezone

    from apps.schools.models import Student, Teacher
    from apps.users.models import User

    cutoff = timezone.now() - timedelta(days=settings.DELETED_ROW_RETENTION_DAYS)

    students = Student.all_objects.filter(deleted_at__lt=cutoff)
    teachers = Teacher.all_objects.filter(deleted_at__lt=cutoff)
    # Read before the delete: the ids are needed afterwards, and the queryset
    # will be empty by then.
    orphaned_users = list(teachers.values_list("user_id", flat=True))

    purged = {"students": students.count(), "teachers": teachers.count()}
    students.hard_delete()
    teachers.hard_delete()

    # A teacher's login has nothing left to authenticate once the teacher is
    # gone, and leaving it behind would hold their email address indefinitely.
    User.objects.filter(pk__in=orphaned_users).delete()

    logger.info("purged soft-deleted rows", extra={**purged, "cutoff": cutoff.isoformat()})
    return purged

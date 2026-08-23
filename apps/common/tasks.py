"""Shared Celery tasks, and the pattern new tasks should follow.

Tasks take primitives (IDs, not model instances) so payloads stay JSON-safe and
never carry stale state, and they set explicit retry policies.
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
def send_email_task(self, subject: str, body: str, recipients: list[str]) -> int:  # noqa: ARG001
    # `self` is unused here, but bind=True keeps it available for self.retry().
    """Send an email out-of-band. Returns the number of messages sent."""
    from django.core.mail import send_mail

    logger.info("sending email", extra={"subject": subject, "recipients": len(recipients)})
    return send_mail(subject=subject, message=body, from_email=None, recipient_list=recipients)

"""Sittings, without pretending a child is a user.

A child has no account, no password and no role, so there is nothing here for
Django's authentication or permission machinery to act on. Modelling one — a
principal that has to claim `is_authenticated` and answer `has_perm` — would
be describing a user that does not exist.

What actually happens is simpler. A child types the paper's code and their own
student id. That pair is checked once, and if it holds, the server issues a
session: a random string the client holds for the rest of the sitting and
sends back on every request. It is a capability, not an identity. Holding it
lets you act on exactly one assignment and nothing else.

The session is per assignment rather than per paper, because the assessment
code is shared by everyone sitting it and on its own could not tell one child
from another.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.assessments.models import AssessmentAssignment

#: The header the client returns the session on, in both the spellings needed:
#: the WSGI key to read it, and the wire name to name it in a challenge.
SESSION_HEADER = "HTTP_X_SITTING_SESSION"
SESSION_HEADER_NAME = "X-Sitting-Session"

#: Not typed by anyone — the child types the assessment code, and this is what
#: the client holds afterwards — so it is long and random rather than legible.
SESSION_BYTES = 32


@dataclass(frozen=True, slots=True)
class OpenedSession:
    code: str
    expires_at: datetime


def _lifetime() -> timedelta:
    return timedelta(hours=settings.SITTING_SESSION_HOURS)


def _fingerprint(code: str) -> str:
    """A plain SHA-256, deliberately.

    The usual reason to reach for a slow password hash is that people choose
    guessable secrets. This one is 256 bits from `secrets`, so there is
    nothing to brute-force, and a fast digest is what lets the session be
    found by an indexed lookup instead of a scan.
    """
    return hashlib.sha256(code.encode()).hexdigest()


def open_session(assignment: AssessmentAssignment) -> OpenedSession:
    """Issue a session for one assignment, replacing any existing one.

    Replacing rather than adding means signing in again on a new device ends
    the old session, which is the behaviour a teacher would expect if a child
    moved tablets mid-paper.
    """
    code = secrets.token_urlsafe(SESSION_BYTES)
    expires_at = timezone.now() + _lifetime()
    assignment.session_hash = _fingerprint(code)
    assignment.session_expires_at = expires_at
    assignment.save(update_fields=["session_hash", "session_expires_at", "updated_at"])
    return OpenedSession(code=code, expires_at=expires_at)


def resolve_session(code: str) -> AssessmentAssignment | None:
    """The assignment a session code stands for, or None.

    Returns None for every failure — unknown, expired, or belonging to a
    student who has since been disabled — so a caller cannot tell which.
    """
    if not code:
        return None
    assignment = (
        AssessmentAssignment.objects.select_related(
            "assessment", "student", "student__school", "student__school_class"
        )
        .filter(session_hash=_fingerprint(code))
        .first()
    )
    if assignment is None or not assignment.student.is_active:
        return None
    if assignment.session_expires_at and timezone.now() > assignment.session_expires_at:
        return None
    return assignment


def close_session(assignment: AssessmentAssignment) -> None:
    """End a sitting immediately. Used when the paper finalises."""
    assignment.session_hash = ""
    assignment.session_expires_at = None
    assignment.save(update_fields=["session_hash", "session_expires_at", "updated_at"])

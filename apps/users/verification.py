"""Issuing and spending the codes that guard sensitive actions.

Every out-of-band confirmation in the product runs through here: verifying a
new school's email, the second factor at sign-in, resetting a password, and
confirming a deletion. Writing them once means the rules that make a
six-digit code safe are applied uniformly rather than remembered per endpoint.

Three of those rules do the work, and none of them is the code's length:

*   **It expires quickly.** Ten minutes is long enough to switch to an email
    client and back, and short enough that a code read over someone's shoulder
    is worthless by the time it could be used.
*   **It is single use, and issuing a new one retires the old.** A code seen in
    an old email cannot be replayed, and a person who clicks "resend" twice
    does not end up with two live codes.
*   **Attempts are counted on the row.** Five wrong guesses burn the code
    rather than the throttle - so an attacker with many IP addresses still
    only gets five tries at any one code.

What is deliberately *not* here is any distinction between "no such account"
and "wrong code". Both come back identically, so neither endpoint can be used
to find out which addresses are registered.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from apps.common.services import BaseService, ValidationError
from apps.users.enums import VerificationPurpose
from apps.users.models import User, VerificationCode

#: Six digits, because a person types it off a phone screen. The number is
#: only safe in company - see the module docstring.
CODE_DIGITS = 6

#: Wrong guesses before the code is spent. Counted per code rather than per
#: address so distributing the attempts across hosts does not buy more of them.
MAX_ATTEMPTS = 5

CODE_TTL = timedelta(minutes=10)

#: Longer than a code's, because the person has already proved they hold the
#: mailbox by this point and is now choosing a password.
HANDLE_TTL = timedelta(minutes=15)

#: Bytes of entropy behind a challenge or reset token. Not typed by anyone.
HANDLE_BYTES = 32

#: What the failure looks like, whatever actually failed.
REJECTED = "That code is not valid. Request a new one."

SUBJECTS: dict[str, str] = {
    VerificationPurpose.REGISTER: "Verify your Koyi account",
    VerificationPurpose.LOGIN: "Your Koyi sign-in code",
    VerificationPurpose.PASSWORD_RESET: "Reset your Koyi password",
    VerificationPurpose.DELETE_TEACHER: "Confirm removing a teacher",
    VerificationPurpose.DELETE_STUDENT: "Confirm removing a student",
}

BODIES: dict[str, str] = {
    VerificationPurpose.REGISTER: (
        "Welcome to Koyi. Enter this code to finish setting up your school:\n\n"
        "    {code}\n\nIt expires in {minutes} minutes."
    ),
    VerificationPurpose.LOGIN: (
        "Enter this code to finish signing in:\n\n    {code}\n\n"
        "It expires in {minutes} minutes. If this was not you, change your "
        "password - someone else knows it."
    ),
    VerificationPurpose.PASSWORD_RESET: (
        "Enter this code to choose a new password:\n\n    {code}\n\n"
        "It expires in {minutes} minutes. If you did not ask for this, ignore "
        "this email; nothing has changed."
    ),
    VerificationPurpose.DELETE_TEACHER: (
        "Enter this code to confirm removing a teacher from your school:\n\n"
        "    {code}\n\nIt expires in {minutes} minutes. Their assessments are "
        "kept; only the person is removed."
    ),
    VerificationPurpose.DELETE_STUDENT: (
        "Enter this code to confirm removing a student from your school:\n\n"
        "    {code}\n\nIt expires in {minutes} minutes. Their results go with "
        "them once the retention period ends."
    ),
}


@dataclass(frozen=True, slots=True)
class IssuedCode:
    """What issuing produced.

    `code` is returned so tests and the admin path can see it; nothing in the
    request path is allowed to put it in a response - it goes to the mailbox
    and nowhere else. `challenge` is the opposite: it is handed to the client
    so the second step does not have to resend credentials.
    """

    code: str
    challenge: str
    expires_at: datetime


def _digits() -> str:
    # `randbelow` rather than `randint`: the code is a credential, so it comes
    # from the CSPRNG like every other secret here.
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def _fingerprint(handle: str) -> str:
    return hashlib.sha256(handle.encode()).hexdigest()


class VerificationService(BaseService):
    """Issues codes and spends them. The only writer of `VerificationCode`."""

    @transaction.atomic
    def issue(
        self,
        *,
        user: User,
        purpose: str,
        subject_id=None,
        email: str = "",
    ) -> IssuedCode:
        """Retire any outstanding code for this purpose and email a new one.

        `email` overrides where it is sent, which only the delete flows use:
        the code confirming a teacher's removal goes to the administrator
        doing the removing, not to the teacher being removed.
        """
        now = timezone.now()
        self._retire(user, purpose)

        code = _digits()
        challenge = secrets.token_urlsafe(HANDLE_BYTES)
        row = VerificationCode.objects.create(
            user=user,
            purpose=purpose,
            code_hash=make_password(code),
            challenge_hash=_fingerprint(challenge),
            subject_id=subject_id,
            expires_at=now + CODE_TTL,
        )
        self._deliver(email or user.email, purpose, code)
        return IssuedCode(code=code, challenge=challenge, expires_at=row.expires_at)

    @transaction.atomic
    def issue_handle(self, *, user: User, purpose: str) -> IssuedCode:
        """A code-less row whose secret is the handle itself.

        Used for the step after a code has already been checked, where the
        person is now doing the thing they were verified for. There is nothing
        to type, so there is nothing to email.
        """
        now = timezone.now()
        self._retire(user, purpose)

        challenge = secrets.token_urlsafe(HANDLE_BYTES)
        row = VerificationCode.objects.create(
            user=user,
            purpose=purpose,
            challenge_hash=_fingerprint(challenge),
            expires_at=now + HANDLE_TTL,
        )
        return IssuedCode(code="", challenge=challenge, expires_at=row.expires_at)

    def verify(
        self,
        *,
        purpose: str,
        code: str,
        user: User | None = None,
        challenge: str = "",
    ) -> VerificationCode:
        """Spend a code. Raises `ValidationError` on every kind of failure.

        Located either by the challenge the client is holding (sign-in) or by
        the user the caller already identified (everything else).
        """
        row = self._locate(purpose=purpose, user=user, challenge=challenge)
        if row is None:
            raise ValidationError(REJECTED)

        if not check_password(code, row.code_hash):
            # Counted before anything else can go wrong, so a wrong guess costs
            # an attempt even if the request fails later.
            row.attempts += 1
            row.save(update_fields=["attempts", "updated_at"])
            raise ValidationError(REJECTED)

        return self._consume(row)

    def redeem_handle(self, *, purpose: str, challenge: str) -> VerificationCode:
        """Spend a handle issued by `issue_handle`."""
        row = self._locate(purpose=purpose, challenge=challenge)
        if row is None:
            raise ValidationError(REJECTED)
        return self._consume(row)

    # --- internals --------------------------------------------------------

    def _locate(
        self,
        *,
        purpose: str,
        user: User | None = None,
        challenge: str = "",
    ) -> VerificationCode | None:
        if not user and not challenge:
            return None

        queryset = VerificationCode.objects.filter(purpose=purpose, consumed_at__isnull=True)
        if challenge:
            queryset = queryset.filter(challenge_hash=_fingerprint(challenge))
        if user is not None:
            queryset = queryset.filter(user=user)

        row = queryset.order_by("-created_at").first()
        if row is None or not row.is_live(timezone.now()):
            return None
        return row

    def _consume(self, row: VerificationCode) -> VerificationCode:
        row.consumed_at = timezone.now()
        row.save(update_fields=["consumed_at", "updated_at"])
        return row

    def _retire(self, user: User, purpose: str) -> None:
        VerificationCode.objects.filter(
            user=user, purpose=purpose, consumed_at__isnull=True
        ).update(consumed_at=timezone.now())

    def _deliver(self, recipient: str, purpose: str, code: str) -> None:
        from apps.common.tasks import send_email_task

        body = BODIES[purpose].format(code=code, minutes=int(CODE_TTL.total_seconds() // 60))
        # `on_commit` so a code is never emailed for a row the transaction
        # subsequently rolls back - a person holding a code nothing recognises
        # is worse than no email at all.
        transaction.on_commit(lambda: send_email_task.delay(SUBJECTS[purpose], body, [recipient]))


__all__ = [
    "CODE_DIGITS",
    "CODE_TTL",
    "MAX_ATTEMPTS",
    "IssuedCode",
    "VerificationService",
]

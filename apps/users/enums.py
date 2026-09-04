"""Enumerations for credentials and the codes that guard them."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class VerificationPurpose(models.TextChoices):
    """What one emailed code is allowed to do.

    A code is bound to exactly one of these. A registration code cannot be
    replayed against the login endpoint, and a code emailed to confirm a
    deletion cannot be spent on a password reset - which matters because all of
    them arrive in the same inbox and look identical.
    """

    REGISTER = "register", _("Verify a new account")
    LOGIN = "login", _("Second factor at sign-in")
    PASSWORD_RESET = "password_reset", _("Reset a forgotten password")
    #: Not emailed. Issued by the reset verify step and spent by the confirm
    #: step, so the new password is set by someone who has already proved they
    #: hold the mailbox rather than by anyone replaying the six digits.
    PASSWORD_RESET_CONFIRM = "password_reset_confirm", _("Set a new password")
    DELETE_TEACHER = "delete_teacher", _("Confirm deleting a teacher")
    DELETE_STUDENT = "delete_student", _("Confirm deleting a student")


__all__ = ["VerificationPurpose"]

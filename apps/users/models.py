from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.common.enums import UserRole
from apps.common.models import BaseModel
from apps.users.enums import VerificationPurpose
from apps.users.managers import UserManager


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    """Email-authenticated user with a UUID primary key.

    This is the single credential store for everyone who logs in: product
    admins, school management accounts and teachers. The domain profile
    (`schools.School`, `schools.Teacher`) hangs off it one-to-one, so password
    hashing, throttling, JWT issuance and the admin all keep working unchanged.

    Students never get a row here — they do not log in with credentials; see
    `apps.student_portal.authentication`.
    """

    email = models.EmailField(_("email address"), unique=True, db_index=True)
    first_name = models.CharField(_("first name"), max_length=150, blank=True)
    last_name = models.CharField(_("last name"), max_length=150, blank=True)

    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_("Unselect this instead of deleting accounts."),
    )
    is_staff = models.BooleanField(_("staff status"), default=False)
    role = models.CharField(
        _("role"),
        max_length=16,
        choices=UserRole.choices,
        default=UserRole.ADMIN,
        db_index=True,
        help_text=_("Which product surface this identity may sign in to."),
    )
    email_verified = models.BooleanField(_("email verified"), default=False)

    last_login_ip = models.GenericIPAddressField(null=True, blank=True, editable=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    # createsuperuser prompts for USERNAME_FIELD + these; email + password is enough.
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["-created_at"]
        constraints = [
            # Belt-and-braces against Alice@x.com / alice@x.com both registering.
            models.UniqueConstraint(Lower("email"), name="user_email_ci_unique"),
        ]
        indexes = [
            models.Index(fields=["role", "is_active"], name="user_role_active_idx"),
        ]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        return super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def get_full_name(self) -> str:
        return self.full_name

    def get_short_name(self) -> str:
        return self.first_name or self.email

    @property
    def is_school_admin(self) -> bool:
        return self.role == UserRole.SCHOOL

    @property
    def is_teacher(self) -> bool:
        return self.role == UserRole.TEACHER


class VerificationCode(BaseModel):
    """One emailed short code, or one issued handle, for one action.

    Everything the product confirms out of band goes through this table:
    verifying a new school, the second factor at sign-in, resetting a
    forgotten password, and confirming a deletion that cannot be undone. They
    differ only in `purpose`, so the checking, expiry, attempt counting and
    single-use rules are written once.

    The two secrets are hashed differently, and the difference is the point.
    `code` is six digits - a million guesses - so it is stored through Django's
    password hashers, which is what makes a stolen database useless. `challenge`
    is 256 bits from `secrets` and is the column a lookup runs against, so it
    gets a plain digest: there is nothing to brute-force, and a slow hash would
    only turn an indexed lookup into a scan.
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="verification_codes",
        verbose_name=_("user"),
    )
    purpose = models.CharField(
        _("purpose"), max_length=32, choices=VerificationPurpose.choices, db_index=True
    )
    code_hash = models.CharField(_("code hash"), max_length=128, blank=True, editable=False)
    challenge_hash = models.CharField(
        _("challenge hash"), max_length=64, blank=True, editable=False, db_index=True
    )

    #: The row the action applies to, for purposes that act on one - the
    #: teacher or student being deleted. A plain UUID rather than a foreign
    #: key: `users` must not depend on `schools`, and a code outliving its
    #: subject by a few minutes is handled by re-fetching, not by a constraint.
    subject_id = models.UUIDField(_("subject"), null=True, blank=True)

    expires_at = models.DateTimeField(_("expires at"))
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    consumed_at = models.DateTimeField(_("consumed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("verification code")
        verbose_name_plural = _("verification codes")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "purpose"], name="verification_user_purpose_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.purpose} for {self.user_id}"

    def is_live(self, now) -> bool:
        """Unspent, unexpired, and not yet out of attempts."""
        from apps.users.verification import MAX_ATTEMPTS

        return self.consumed_at is None and self.expires_at > now and self.attempts < MAX_ATTEMPTS

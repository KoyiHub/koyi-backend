from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.common.enums import UserRole
from apps.common.models import BaseModel
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

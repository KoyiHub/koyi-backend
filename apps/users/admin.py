from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from apps.users.models import User


class UserCreationFormCustom(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email",)


class UserChangeFormCustom(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeFormCustom
    add_form = UserCreationFormCustom

    list_display = ["email", "full_name", "is_active", "is_staff", "email_verified", "created_at"]
    list_filter = ["is_active", "is_staff", "is_superuser", "email_verified", "created_at"]
    search_fields = ["email", "first_name", "last_name"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at", "last_login", "last_login_ip"]

    fieldsets = (
        (None, {"fields": ("id", "email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "email_verified",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            _("Important dates"),
            {"fields": ("last_login", "last_login_ip", "created_at", "updated_at")},
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "is_staff", "is_superuser"),
            },
        ),
    )

    @admin.display(description=_("full name"), ordering="first_name")
    def full_name(self, obj: User) -> str:
        return obj.full_name

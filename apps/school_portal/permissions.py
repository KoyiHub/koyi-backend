"""Permissions for the school management dashboard.

The shared implementations live in `apps.common.permissions`; re-exporting them
here means a view in this package imports its permissions from its own package,
and that tightening a rule for schools only is a one-file change.
"""

from apps.common.permissions import (
    IsSchoolAdmin,
    IsVerifiedSchoolAdmin,
    SchoolScopedMixin,
)

__all__ = ["IsSchoolAdmin", "IsVerifiedSchoolAdmin", "SchoolScopedMixin"]

"""Fixtures for the school management dashboard tests."""

import pytest

from apps.schools.factories import SchoolFactory


@pytest.fixture(autouse=True)
def _run_on_commit_callbacks(monkeypatch):
    """Fire `on_commit` callbacks immediately.

    Codes are emailed from `transaction.on_commit`, so that nobody is handed a
    code for a row the request then rolls back. A test wraps everything in a
    transaction that is never committed, so without this the mailbox stays
    empty and every assertion about a code fails for a reason that has nothing
    to do with what is being tested.
    """
    monkeypatch.setattr(
        "django.db.transaction.on_commit",
        lambda func, using=None: func(),  # noqa: ARG005
    )


@pytest.fixture
def school():
    return SchoolFactory()


@pytest.fixture
def school_client(api_client, school):
    """A client signed in as school management.

    Mints the token directly rather than walking the two-step login: what the
    login flow does is tested in `test_auth.py`, and repeating it in every
    other fixture would make those tests depend on it.
    """
    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken.for_user(school.user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api_client

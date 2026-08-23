import pytest
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError

from apps.users.factories import UserFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_create_user_sets_hashed_password():
    user = User.objects.create_user(email="a@example.com", password="s3cret-pass!")

    assert user.password != "s3cret-pass!"
    assert user.check_password("s3cret-pass!")
    assert user.is_staff is False
    assert user.is_superuser is False


def test_create_superuser_flags():
    admin = User.objects.create_superuser(email="admin@example.com", password="s3cret-pass!")

    assert admin.is_staff
    assert admin.is_superuser
    assert admin.email_verified


def test_email_is_required():
    with pytest.raises(ValueError, match="email address"):
        User.objects.create_user(email="", password="x")


def test_email_is_normalised_to_lowercase():
    user = UserFactory(email="Mixed.Case@Example.COM")

    assert user.email == "mixed.case@example.com"


def test_email_uniqueness_is_case_insensitive():
    UserFactory(email="dup@example.com")

    with pytest.raises(IntegrityError):
        User.objects.create(email="DUP@example.com")


def test_full_name_falls_back_to_email():
    user = UserFactory(first_name="", last_name="")

    assert user.full_name == user.email


def test_str_is_the_email():
    user = UserFactory(email="who@example.com")

    assert str(user) == "who@example.com"

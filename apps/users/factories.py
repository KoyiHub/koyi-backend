import factory
from django.contrib.auth import get_user_model
from factory.django import DjangoModelFactory

User = get_user_model()

DEFAULT_PASSWORD = "test-password-123!"  # noqa: S105


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ["email"]
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    is_active = True
    email_verified = True

    @factory.post_generation
    def password(obj, create: bool, extracted: str | None, **kwargs):  # noqa: N805, ARG002
        if not create:
            return
        obj.set_password(extracted or DEFAULT_PASSWORD)
        obj.save(update_fields=["password"])


class StaffUserFactory(UserFactory):
    is_staff = True


class SuperUserFactory(UserFactory):
    is_staff = True
    is_superuser = True

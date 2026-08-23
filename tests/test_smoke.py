"""Project-wide smoke tests — things that should never silently break."""

import pytest
from django.core.management import call_command


def test_openapi_schema_generates_without_errors():
    """A broken schema means broken client generation and docs."""
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)

    assert schema["info"]["title"] == "Koyi API"
    assert "/api/v1/auth/login/" in schema["paths"]


@pytest.mark.django_db
def test_no_missing_migrations():
    """Fails when a model change hasn't been captured in a migration."""
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


@pytest.mark.django_db
def test_celery_tasks_are_registered():
    from config.celery import app

    app.loader.import_default_modules()

    assert "apps.common.tasks.send_email_task" in app.tasks

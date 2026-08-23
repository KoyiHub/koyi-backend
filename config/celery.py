import os

from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("koyi")

# All CELERY_-prefixed Django settings become Celery config.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@setup_logging.connect
def configure_logging(**_kwargs) -> None:
    """Use Django's LOGGING config instead of Celery's own."""
    from logging.config import dictConfig

    from django.conf import settings

    dictConfig(settings.LOGGING)


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> None:
    """Smoke-test task: `celery -A config call config.celery.debug_task`."""
    print(f"Request: {self.request!r}")  # noqa: T201

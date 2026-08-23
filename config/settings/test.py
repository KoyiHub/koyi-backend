"""Settings for the test suite — optimised for speed and determinism."""

from .base import *
from .base import MIDDLEWARE

DEBUG = False

# Whitenoise only serves collected static files, which tests never exercise —
# and it warns (fatally, given filterwarnings=error) when staticfiles/ is absent.
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]
SECRET_KEY = "django-insecure-test-key-not-a-secret"  # noqa: S105
ALLOWED_HOSTS = ["*"]

# Always an in-memory SQLite DB, regardless of what DATABASE_URL says, so a
# stray env var can never point the suite at a real database.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "ATOMIC_REQUESTS": True,
        "TEST": {"NAME": ":memory:"},
    },
}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# The slowest thing in most Django test suites is password hashing.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Tasks run inline; nothing touches a broker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Throttling makes tests flaky and order-dependent.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": (),
    "DEFAULT_THROTTLE_RATES": {},
}

# Keep test output readable.
LOGGING["root"]["level"] = "WARNING"

# Migrations are already exercised in CI; skipping them speeds up local runs.
# Enable with: pytest --no-migrations  (pytest-django flag)

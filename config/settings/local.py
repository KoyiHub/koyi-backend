"""Local development settings. Never use these in a deployed environment."""

from .base import *
from .base import BASE_DIR, INSTALLED_APPS, MIDDLEWARE, env

DEBUG = True

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-local-only-do-not-deploy")

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]  # noqa: S104

# Emails land in the console instead of a real SMTP server.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Any origin is fine on a laptop; production sets an explicit allowlist.
CORS_ALLOW_ALL_ORIGINS = True

# --- Developer tooling -----------------------------------------------------
INSTALLED_APPS = [*INSTALLED_APPS, "django_extensions", "debug_toolbar"]
MIDDLEWARE = [
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    # django.contrib.staticfiles serves static files in DEBUG, so whitenoise
    # would only warn about the absent (uncollected) staticfiles/ directory.
    *(m for m in MIDDLEWARE if "whitenoise" not in m),
]

INTERNAL_IPS = ["127.0.0.1", "::1"]

DEBUG_TOOLBAR_CONFIG = {
    # The toolbar's HTML injection breaks JSON API responses, so only show it
    # when the response is actually a browsable page.
    "SHOW_TOOLBAR_CALLBACK": lambda request: (
        DEBUG
        and not request.path.startswith("/api/")
        and "text/html" in request.headers.get("Accept", "")
    ),
    "RESULTS_CACHE_SIZE": 100,
}

# Browsable API is genuinely useful while developing.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ),
}

# Fast hashing keeps `createsuperuser` / fixtures snappy.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]

# Uncompressed static files — no `collectstatic` needed to run the dev server.
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Run Celery tasks inline unless a broker is explicitly configured.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True

MEDIA_ROOT = BASE_DIR / "media"

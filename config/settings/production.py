"""Production settings. Every secret must come from the environment."""

from .base import *
from .base import env

DEBUG = False

# No default — the process must fail loudly if the secret is missing.
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

# --- HTTPS / cookies -------------------------------------------------------
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 365)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SAMESITE = "Lax"

# --- Logging ---------------------------------------------------------------
LOGGING["handlers"]["console"]["formatter"] = "json"

# --- Email -----------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("DJANGO_EMAIL_HOST", default="")
EMAIL_PORT = env.int("DJANGO_EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 10

# --- API -------------------------------------------------------------------
# No browsable API in production: it is a large attack/perf surface.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
}

ADMINS = [("Koyi Ops", env("DJANGO_ADMIN_EMAIL", default="ops@koyi.local"))]
MANAGERS = ADMINS

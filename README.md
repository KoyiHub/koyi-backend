# Koyi Backend

Django 5.2 + Django REST Framework API, managed with [uv](https://docs.astral.sh/uv/).

## Quick start

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) — it installs
Python 3.13 for you, so nothing else is needed.

```bash
make setup     # venv, dependencies, .env, migrations, git hooks
make run       # dev server on http://127.0.0.1:8000
```

Then:

| URL | What |
| --- | --- |
| http://127.0.0.1:8000/api/docs/ | Swagger UI |
| http://127.0.0.1:8000/api/redoc/ | ReDoc |
| http://127.0.0.1:8000/api/schema/ | OpenAPI 3 schema |
| http://127.0.0.1:8000/admin/ | Django admin (`make superuser` first) |
| http://127.0.0.1:8000/healthz/ | Liveness probe |
| http://127.0.0.1:8000/readyz/ | Readiness probe (checks the DB) |

`make help` lists every command.

## Layout

```
config/               Project configuration
  settings/
    base.py           Shared settings — no environment assumptions
    local.py          Development (DEBUG, debug toolbar, browsable API)
    test.py           Test suite (in-memory DB, eager Celery, fast hashing)
    production.py     Deployment (strict security, JSON logs)
  urls.py             Root URLconf: admin, probes, docs
  api_urls.py         /api/v1/ routes
  celery.py           Celery application
apps/
  common/             Shared building blocks — see below
  users/              Custom user model + JWT auth endpoints
tests/                Cross-cutting smoke tests
conftest.py           Fixtures available to every test
```

### What `apps/common` gives you

| Module | Use it for |
| --- | --- |
| `models.py` | `BaseModel` (UUID pk + timestamps), `TimeStampedModel`, `SoftDeleteModel` |
| `pagination.py` | `DefaultPagination` (page numbers), `TimestampCursorPagination` (large feeds) |
| `exceptions.py` | `ApplicationError` / `ConflictError` to raise from domain code |
| `middleware.py` | Request IDs — `get_request_id()` anywhere in a request |
| `tasks.py` | The Celery task pattern to copy |

New models should subclass `apps.common.models.BaseModel` unless there's a
reason not to.

## Configuration

All configuration comes from the environment. `make setup` copies
`.env.example` to `.env`; `.env` is git-ignored and is for local use only —
deployed environments inject real environment variables instead.

Settings are chosen by `DJANGO_SETTINGS_MODULE`, which defaults to
`config.settings.local` in `manage.py`, `config.settings.test` in pytest, and
`config.settings.production` in `wsgi.py` / `asgi.py`.

The database is read from `DATABASE_URL`. It defaults to SQLite; point it at
Postgres when you want production parity:

```bash
DATABASE_URL=postgres://koyi:koyi@localhost:5432/koyi
```

`docker-compose.yml` can start that Postgres (uncomment it) and Redis, but
nothing about day-to-day development requires Docker.

## API conventions

**Errors.** Every 4xx/5xx uses one envelope, so clients parse one shape:

```json
{
  "error": {
    "type": "validation_error",
    "message": "Request could not be processed.",
    "detail": { "email": ["A user with this email already exists."] },
    "request_id": "9f2c1a..."
  }
}
```

Raise `ApplicationError` (or a subclass) from services and it becomes this
automatically — views don't catch-and-translate.

**Request IDs.** Every request gets an `X-Request-ID`, reusing an upstream one
if present. It's echoed in the response and attached to every log line emitted
while handling that request, so a log line traces back to a request.

**Auth.** JWT via `Authorization: Bearer <access>`. Access tokens last 15
minutes, refresh tokens 7 days and rotate on use, with the old one blacklisted.

```
POST /api/v1/auth/register/         Create an account
POST /api/v1/auth/login/            → { access, refresh, user }
POST /api/v1/auth/logout/           Blacklist a refresh token
POST /api/v1/auth/token/refresh/    Rotate the token pair
GET  /api/v1/auth/me/               Current user
POST /api/v1/auth/password/change/  Change own password
```

**Defaults.** Endpoints require authentication unless they opt out with
`permission_classes = [AllowAny]`, and responses are paginated at 25 per page.

## Background work

Celery tasks run inline in tests, and in development too if you set
`CELERY_TASK_ALWAYS_EAGER=True`. To run them for real:

```bash
make services   # Redis, via Docker
make worker     # in one terminal
make beat       # in another, for scheduled tasks
```

Schedules are stored in the database (`django_celery_beat`) and edited through
the admin, not in code.

## Development

```bash
make test        # pytest, parallel
make test-cov    # + coverage report (fails under 80%)
make format      # ruff: auto-fix and format
make lint        # ruff: check only
make typecheck   # mypy
make check       # lint + types + deploy check + missing migrations
```

Pre-commit hooks run ruff, mypy, gitleaks, and a missing-migration check on
every commit; `make setup` installs them. Run everything manually with
`uv run pre-commit run --all-files`.

**Tests** live in `apps/<app>/tests/`, use the fixtures in `conftest.py`
(`user`, `api_client`, `auth_client`, `as_user`) and the factories in
`apps/<app>/factories.py`. `filterwarnings = error` is on, so a new deprecation
warning fails the build rather than accumulating.

**Adding an app:**

```bash
uv run python manage.py startapp thing apps/thing
```

Then set `name = "apps.thing"` in its `AppConfig`, add it to `LOCAL_APPS` in
`config/settings/base.py`, and include its URLs from `config/api_urls.py`.

## Deployment

`config/settings/production.py` is validated by CI against Django's deployment
checklist (`manage.py check --deploy`), which currently passes clean.

Required environment variables: `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`,
`DATABASE_URL`, `CELERY_BROKER_URL`. Generate a secret key with `make secret`.

```bash
uv sync --no-dev
uv run python manage.py migrate
uv run python manage.py collectstatic --noinput
uv run gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4
```

Static files are served by WhiteNoise, so no separate web server is needed for
them. Set `DJANGO_LOG_FORMAT=json` for structured logs.

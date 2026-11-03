# cardioanalysis-api

[![Pipeline](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardioanalysis-api/badges/main/pipeline.svg)](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardioanalysis-api/-/pipelines?ref=main)
[![Coverage](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardioanalysis-api/badges/main/coverage.svg)](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardioanalysis-api/-/pipelines?ref=main)
[![Latest release](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardioanalysis-api/-/badges/release.svg)](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardioanalysis-api/-/releases)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardioanalysis-api/-/blob/main/LICENCE)

REST API exposing the [cardiolab](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardiolab) HRV analysis toolkit as an HTTP service.

Built with **FastAPI** and **Python 3.13**. Designed as a thin routing and validation layer on top of cardiolab — all scientific computation stays in the library.

## Requirements

- Python 3.13+
- A Supabase instance (Auth + PostgreSQL), self-hosted or not — see [`example/auth/`](example/auth/)
- cardiolab >= 0.3.0

## Installation

Dependencies are managed with [uv](https://docs.astral.sh/uv/) (version `0.13.6`, same as the CI). uv installs Python 3.13 itself if needed (`.python-version`).

```bash
# Development: creates .venv with runtime dependencies + the "dev" group
# (pytest, pytest-cov, httpx2, ruff), exactly as pinned in uv.lock
uv sync

# Runtime only (no dev tools), as in the Docker image
uv sync --no-dev
```

Dependencies are declared in `pyproject.toml` (`[project].dependencies`, `[project.optional-dependencies]` for extras, `[dependency-groups]` for dev tools) and pinned in `uv.lock`, which is committed. Never edit `uv.lock` by hand: use `uv add`, `uv remove` or `uv lock`.


## Configuration

The application is configured through environment variables. Locally, they are read from a `.env` file at the project root (never committed):

```bash
cp .env.example .env
```

| Variable | Description | Example |
|----------|-------------|---------|
| `DB_HOST` | PostgreSQL host of the application database | `localhost` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `DB_NAME` | Database name | `cardioanalysis_database` |
| `DB_USER` | Database user | `user` |
| `DB_PASSWORD` | Database password | — |
| `DB_SCHEMA` | Application schema: API tables and migration tracking (see [Database migrations](#database-migrations)). Default: `cardioanalysis` | `cardioanalysis` |
| `SUPABASE_URL` | URL of the Supabase API gateway | `http://localhost:8000` |
| `SUPABASE_JWT_SECRET` | `JWT_SECRET` of the Supabase stack, used to verify access tokens (HS256) | — |
| `DB_HOST_TEST`, `DB_PORT_TEST`, `DB_NAME_TEST`, `DB_USER_TEST`, `DB_PASSWORD_TEST` | Test database for integration tests (see [Development](#development)) | `localhost`, `5432`… |
| `ENV_FILE` | Alternative env file to load instead of `.env` | `.env.nas` |
| `SUPABASE_DIR`, `SUPABASE_ENV_FILE` | Local Supabase stack started by `tests/conftest.py` for integration tests (folder relative to the project root, env file relative to that folder). Defaults: `../cardioanalysis-supabase-local-nas`, `local/.env` | `local/auth`, `.env` (stack from `example/auth`) |

> With a self-hosted Supabase, the port exposed on the host is the Supavisor pooler's: the user must be in the `<role>.<POOLER_TENANT_ID>` format.

### Self-hosted Supabase (authentication and database)

The API verifies JWTs issued by Supabase Auth and stores its data in Supabase's PostgreSQL. A ready-to-deploy self-hosted Supabase stack (Docker Compose, no secrets included) is provided in [`example/auth/`](example/auth/). Its guide, [`example/auth/README.md`](example/auth/README.md) (in French), covers key generation, start modes (full, minimal without Studio, pgAdmin), user creation, connecting this API, running the stack only for tests or permanently, and server security.

The application version (`/health`, OpenAPI docs) is not configured: it comes from `version` in `pyproject.toml`.

## Running

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000` — interactive documentation at `http://localhost:8000/docs`.
If the local Supabase gateway already uses port 8000, add `--port 8080`.

To point at another backend (e.g. the NAS): `ENV_FILE=.env.nas uvicorn app.main:app --reload`.

### With Docker

The image is built, scanned and published by the GitLab CI pipeline on `V-x.y.z` tags (`registry.gitlab.com/project-jmilon-devsecops/cardioanalysis/cardioanalysis-api:<version>`). To build and run it locally:

```bash
docker build -t cardioanalysis-api .
# docker run --env-file does not strip quotes: use an env file without quotes around values
docker run --rm -p 8080:8000 --env-file .env.docker cardioanalysis-api
```

The image runs as a non-root user and exposes a `HEALTHCHECK` on `/health`. No `.env` file is embedded in the image (see `.dockerignore`).

### Database migrations

The API's own tables are versioned by the SQL files of [`app/migrations/`](app/migrations/) (`A<nnn>__<description>.sql`, rules in its README). cardiolab's tables are not part of them: cardiolab applies its own migrations. Apply the pending ones before starting a new version:

```bash
uv run python -m app.migrate               # application migrations, with DB_USER
uv run python -m app.migrate --status      # applied / pending, changes nothing
ENV_FILE=.env.admin uv run python -m app.migrate --privileged   # administrator role
```

* Each migration runs in its own transaction; a failing one is rolled back and the command exits with code 1.
* A migration that touches the `auth` schema (trigger on `auth.users`, signup hook) is **privileged** (`.privileged.sql`): it is applied only with `--privileged`, by an administrator role, never by the application role. A run stops at the first migration of the other kind, so the order is always kept.
* Applied migrations are recorded, with a checksum, in `<DB_SCHEMA>.api_schema_migrations`; editing an applied migration stops the next run.
* `--all` applies both kinds with a single role: for disposable databases only (the CI uses it before the tests).

With Docker: `docker run --rm --env-file .env.docker cardioanalysis-api python -m app.migrate`.


## Project structure

```
cardioanalysis-api/
├── app/                     # Main Python package
│   ├── main.py              # FastAPI app, router registration (/v1 prefix)
│   ├── config.py            # Settings loaded from environment variables / .env
│   ├── migrate.py           # Command line: python -m app.migrate
│   ├── migrations/          # SQL migrations of the API's tables (A<nnn>__*.sql)
│   ├── auth/                # Supabase JWT verification (get_current_user dependency)
│   ├── routers/             # HTTP endpoints — no business logic
│   │   ├── health.py        # GET /health
│   │   └── auth.py          # GET /v1/me
│   ├── schemas/             # Pydantic models — request/response contracts
│   └── services/            # Application logic — only layer that imports cardiolab
│       ├── db.py            # PostgreSQL access, get_repository(), get_connection()
│       └── migrations.py    # Migration discovery and runner
├── tests/                   # Unit tests + integration tests (marker "integration")
├── example/auth/            # Self-hosted Supabase stack to deploy (see its README)
├── Dockerfile               # Production image (multi-stage, non-root)
├── .dockerignore
├── .gitlab-ci.yml           # Centralised pipeline (component python-fastapi)
├── CHANGELOG.md
└── pyproject.toml           # Metadata, dependencies, ruff/pytest/coverage config
```

Planned modules are listed in the [Roadmap](#roadmap).


## API overview

| Method | Endpoint  | Auth     | Description                              |
|--------|-----------|----------|------------------------------------------|
| GET    | `/health` | —        | Service health check and version         |
| GET    | `/v1/me`  | Bearer   | Current authenticated user + HRV profile |

Business routes are versioned under `/v1`; `/health` stays at the root (Docker healthcheck, monitoring).

Protected routes expect `Authorization: Bearer <access token>`, the token being issued by Supabase Auth (`/auth/v1/token`). Missing, invalid or expired token → `401`. The user is always identified by the token, never by an id in the path or query (only future `/v1/admin/...` routes will take an account id).

Planned endpoints are listed in the [Roadmap](#roadmap).

## Development

```bash
# Lint
uv run ruff check .
uv run ruff format --check .

# Tests (coverage measured on app/, minimum 80 %)
uv run pytest --cov --cov-report=term-missing

# Dependencies
uv add <package>             # runtime dependency
uv add --dev <package>       # dev tool
uv lock --upgrade-package <package> && uv sync   # upgrade one dependency
```

The CI installs with `uv sync --frozen`: it fails if `uv.lock` is out of date with `pyproject.toml` (run `uv lock` and commit both files).

Integration tests (marker `integration`) run when `DB_HOST_TEST` is set. Locally, `tests/conftest.py` starts the minimal Supabase stack (`db`, `supavisor`) if needed, from `SUPABASE_DIR` (see [`example/auth/README.md`](example/auth/README.md), section 8). In GitLab CI, a disposable `postgres` service is used instead (`PYTEST_DATABASE: "postgres"` in `.gitlab-ci.yml`).


## Roadmap

| Version | Content |
|---------|---------|
| v0.1.0  | ✅ Project scaffold, `/health`, GitLab CI |
| v0.2.0  | ✅ Authentication with a self-hosted Supabase (JWT verification, `/me`), Docker image, Python 3.13 and uv |
| v0.3.0  | **Accounts and beta**: `/v1` prefix and API database migrations, accounts and statuses, administration and audit, account deletion (`DELETE /me`), consents, beta survey, registration hook and waiting list, feature flags, e-mails |
| v0.4.0  | **Profile and import**: `PUT /me/profile`, file import in two steps (preview, then commit) using the cardiolab parsers (Polar, HRV4Training, Garmin, Apple Health) and analyses (resting, orthostatic), `/measurements`, raw file storage |
| v0.5.0  | **Fitness**: readiness (today and history), baseline (`/me/baseline`), monitoring, CSV export |
| v0.6.0  | **Training**: workouts, training load (ATL/CTL/TSB), calendar, sports reference |
| v0.7.0  | **GDPR and beta**: personal data export, purge after 30 days, consent renewal, beta statistics — **beta opening** |
| v0.8.0  | **Tests**: summaries, history and comparison for the cardiolab protocols (coherence, HRR, drift, VO2max...) |
| later   | Alerts, training zones, dashboard, sensors |

See [CHANGELOG.md](CHANGELOG.md) for what each release contains.

## License

AGPLv3 — see [LICENCE](LICENCE).

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-11-03

Content of milestone **V-0.2.0**: authentication with a self-hosted Supabase.

### Added — Authentication (Supabase Auth)

* **`app/auth/`**: FastAPI dependency `get_current_user()` verifying the access tokens issued
  by Supabase Auth. Verification is purely cryptographic (HS256 signature checked against
  `SUPABASE_JWT_SECRET`, audience `authenticated`): no network call to Supabase on each
  request. The static `anon` / `service_role` API keys carry no audience and are rejected.
  Missing or malformed `Authorization` header, invalid or expired token → **401**.
* **`GET /me`** (protected): the authenticated user (`id`, `email`, `role`) plus their HRV
  profile read from cardiolab's `user_profiles` table (`primary_protocol`, `sex`,
  `birth_year`, `height_cm`, `hr_max`, `hr_rest`, `weight_kg`). A missing profile is not an
  error (`profile: null`); database unreachable → **503**.
* **`app/services/db.py`**: `get_repository()`, the single place where cardiolab's
  `HRVRepository` is imported (PostgreSQL access).
* **Configuration**: `SUPABASE_URL`, `SUPABASE_JWT_SECRET` and `DB_HOST` / `DB_PORT` /
  `DB_NAME` / `DB_USER` / `DB_PASSWORD`. `ENV_FILE` loads another env file instead of `.env`
  (e.g. `ENV_FILE=.env.nas` to point at another backend).

### Added — Self-hosted Supabase stack (`example/auth/`)

* Generic, secret-free Docker Compose stack based on the official Supabase self-hosting
  setup, with its guide in French (`example/auth/README.md`): key generation, start modes
  (full, minimal without Studio, pgAdmin), user creation, connecting this API, running the
  stack only for tests or permanently, server security.
* The guide shows how to create an application role limited to its own schema (not a
  superuser). With Supavisor (the pooler exposed on the host), the database user is
  `<role>.<POOLER_TENANT_ID>`.

### Added — Tests

* Unit tests of the JWT dependency and of `/me` (repository mocked, tokens signed with a
  test secret).
* Integration tests (marker `integration`) against a real PostgreSQL, in an isolated table
  created and dropped by each test; skipped when `DB_HOST_TEST` is not set.
* Locally, `tests/conftest.py` starts the minimal Supabase stack (`db`, `supavisor`) when
  integration tests are collected, and stops it at the end if it started it. Its location is
  set by `SUPABASE_DIR` / `SUPABASE_ENV_FILE` (defaults: `../cardioanalysis-supabase-local-nas`
  and `local/.env`; `local/auth` and `.env` for a stack copied from `example/auth`).
* 12 tests, 95 % coverage of `app/` (minimum 80 %).

### Added — Docker image

* Multi-stage `Dockerfile` (`python:3.13-slim`), non-root user, `HEALTHCHECK` on `/health`,
  bytecode compiled at build time (`UV_COMPILE_BYTECODE=1`: faster container start).
  `.dockerignore` keeps every `.env` file out of the image.
* Built, scanned and published to the project's GitLab registry by the CI on `V-x.y.z` tags.

### Changed — Python 3.13 and uv

* **Python 3.13 minimum** (`requires-python = ">=3.13"`, `.python-version`).
* **uv** manages the dependencies: `uv.lock` committed, development tools in the `dev`
  dependency group, installed with `uv sync`. The CI installs with `uv sync --frozen` and
  fails if `uv.lock` is out of date.

### Changed — CI

* Centralised pipeline included as the **component `python-fastapi@1.0.0`** (instead of
  `include: project`). Every input is written explicitly, defaults included, so the active
  checks can be read in `.gitlab-ci.yml`: version check, Ruff lint and format, SAST,
  dependency scan, Dockerfile check and image scan are all blocking.
* Integration tests run in the CI against a disposable `postgres` service.

### Fixed

* `app/config.py` now loads `.env` from the project root instead of the current working
  directory (it was never loaded when the app was started from another folder).

## [0.1.0] - 2026-07-17

### Added

* Project scaffold: `app/` package (`routers`, `schemas`, `services`), configuration from
  environment variables, application version read from `pyproject.toml`.
* `GET /health`: service health check, with its Pydantic response model and unit test.
* Ruff and pre-commit.
* GitLab CI using the centralised pipeline `pipeline-python-centralisee`.

# cardioanalysis-api

REST API exposing the [cardiolab](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardiolab) HRV analysis toolkit as an HTTP service.

Built with **FastAPI** and **Python 3.12**. Designed as a thin routing and validation layer on top of cardiolab — all scientific computation stays in the library.

## Requirements

- Python 3.12+
- PostgreSQL (v0.4.0+)
- cardiolab >= 0.3.0

## Installation

```bash
# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Development: runtime dependencies + test/lint tools (pytest, ruff, httpx2), editable mode
pip install -e ".[dev]"

# Or runtime only
pip install .
```

All dependencies are declared in `pyproject.toml` (`[project].dependencies`, `[project.optional-dependencies]`).


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
| `SUPABASE_URL` | URL of the Supabase API gateway | `http://localhost:8000` |
| `SUPABASE_JWT_SECRET` | `JWT_SECRET` of the Supabase stack, used to verify access tokens (HS256) | — |
| `DB_HOST_TEST`, `DB_PORT_TEST`, `DB_NAME_TEST`, `DB_USER_TEST`, `DB_PASSWORD_TEST` | Test database for integration tests (see [Development](#development)) | `localhost`, `5432`… |
| `ENV_FILE` | Alternative env file to load instead of `.env` | `.env.nas` |

> With a self-hosted Supabase, the port exposed on the host is the Supavisor pooler's: the user must be in the `<role>.<POOLER_TENANT_ID>` format.

The application version (`/health`, OpenAPI docs) is not configured: it comes from `version` in `pyproject.toml`.
                                       |

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


## Project structure

```
cardioanalysis-api/
├── app/                     # Main Python package
│   ├── main.py              # FastAPI app, router registration
│   ├── config.py            # Settings loaded from environment variables / .env
│   ├── auth/                # Supabase JWT verification (get_current_user dependency)
│   ├── routers/             # HTTP endpoints — no business logic
│   │   ├── health.py        # GET /health
│   │   └── auth.py          # GET /me
│   ├── schemas/             # Pydantic models — request/response contracts
│   └── services/            # Application logic — only layer that imports cardiolab
│       └── db.py            # PostgreSQL access, get_repository()
├── tests/                   # Unit tests + integration tests (marker "integration")
├── Dockerfile               # Production image (multi-stage, non-root)
├── .dockerignore
├── .gitlab-ci.yml           # Centralised pipeline (pipeline-python-centralisee)
└── pyproject.toml           # Metadata, dependencies, ruff/pytest/coverage config
```

Planned modules (`parse`, `analyze`, `sessions`, `analytics`) are listed in the [Roadmap](#roadmap).


## API overview

| Method | Endpoint                        | Description                            |
|--------|---------------------------------|----------------------------------------|
| GET    | `/health`                       | Service health check                   |
| GET    | `/me`                           | Current authenticated user + HRV profile |
| POST   | `/parse/polar`                  | Parse Polar RR file (.txt/.csv/.rr)    |
| POST   | `/parse/hrv4training`           | Parse HRV4Training export (.csv)       |
| POST   | `/parse/apple-health`           | Parse Apple Health export (.xml)       |
| POST   | `/parse/garmin/fit`             | Parse Garmin FIT file                  |
| POST   | `/parse/garmin/csv`             | Parse Garmin Connect CSV               |
| POST   | `/analyze/resting`              | Resting HRV analysis                   |
| POST   | `/analyze/orthostatic`          | Orthostatic protocol analysis          |
| GET    | `/sessions`                     | List sessions                          |
| POST   | `/sessions`                     | Create a session                       |
| GET    | `/sessions/{id}`                | Get session detail                     |
| DELETE | `/sessions/{id}`                | Delete a session                       |
| GET    | `/baseline/{user_id}`           | User HRV baseline                      |
| GET    | `/analytics/readiness`          | Readiness score time series            |
| GET    | `/analytics/training-load`      | ATL/CTL/TSB training load              |

## Development

```bash
# Lint
ruff check .
ruff format --check .

# Tests (coverage measured on app/, minimum 80 %)
pytest --cov --cov-report=term-missing
```

Integration tests (marker `integration`) run when `DB_HOST_TEST` is set. Locally, `tests/conftest.py` starts the minimal Supabase stack (`db`, `supavisor`) if needed. In GitLab CI, a disposable `postgres` service is used instead (`PYTEST_DATABASE: "postgres"` in `.gitlab-ci.yml`).


## Roadmap

| Version | Content                                      |
|---------|----------------------------------------------|
| v0.1.0  | Project scaffold, `/health`, GitLab CI       |
| v0.2.0  | Authentication & user management (Supabase)  |
| v0.3.0  | Sensor parsers (Polar, HRV4Training, Garmin, Apple Health) |
| v0.4.0  | HRV analysis endpoints (resting, orthostatic)|
| v0.5.0  | Supabase Postgres persistence, sessions CRUD, data migration |
| v0.6.0  | Analytics (baseline, readiness, training load)|
| v0.7.0+ | Other cardiolab protocols (coherence, HRR, drift, vo2max) |

## License

AGPLv3 — see [LICENCE](LICENCE).

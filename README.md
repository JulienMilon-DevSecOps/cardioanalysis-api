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

# Install runtime dependencies
pip install -r requirements.txt

# Install dev dependencies
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable       | Description                          | Example                                          |
|----------------|--------------------------------------|--------------------------------------------------|
| `DATABASE_URL` | PostgreSQL connection string         | `postgresql://user:pass@localhost:5432/cardioanalysis` |
| `APP_VERSION`  | Application version (auto-populated) | `0.1.0`                                          |

## Running

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.
Interactive documentation: `http://localhost:8000/docs`

## Project structure

```
cardioanalysis-api/
├── app/                     # Main Python package
│   ├── main.py              # FastAPI app, router registration, lifespan
│   ├── config.py            # Settings loaded from .env
│   ├── routers/             # HTTP endpoints — no business logic
│   │   ├── health.py        # GET /health
│   │   ├── parse.py         # POST /parse/polar, /hrv4training, /apple-health, /garmin/*
│   │   ├── analyze.py       # POST /analyze/resting, /orthostatic
│   │   ├── sessions.py      # CRUD /sessions
│   │   └── analytics.py     # GET /baseline/{user_id}, /analytics/readiness, /training-load
│   ├── schemas/             # Pydantic models — request/response contracts
│   │   ├── parse.py
│   │   ├── analyze.py
│   │   ├── sessions.py
│   │   └── analytics.py
│   └── services/            # Application logic — only layer that imports cardiolab
│       ├── parse.py         # Temp file handling + cardiolab sensor parsers
│       ├── analyze.py       # RRSeries construction + cardiolab protocols
│       ├── db.py            # PostgreSQL connection, get_repository() dependency
│       └── analytics.py     # Baseline, readiness score, training load
├── tests/
├── build/                   # Dockerfile, docker-compose.yml
├── pyproject.toml
└── requirements.txt
```

## API overview

| Method | Endpoint                        | Description                            |
|--------|---------------------------------|----------------------------------------|
| GET    | `/health`                       | Service health check                   |
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

# Tests
pytest
pytest --cov=cardioanalysis_api --cov-report=term-missing
```

## Roadmap

| Version | Content                                      |
|---------|----------------------------------------------|
| v0.1.0  | Project scaffold, `/health`, GitLab CI       |
| v0.2.0  | Sensor parsers (Polar, HRV4Training, Garmin, Apple Health) |
| v0.3.0  | HRV analysis endpoints (resting, orthostatic)|
| v0.4.0  | PostgreSQL persistence, sessions CRUD        |
| v0.5.0  | Analytics (baseline, readiness, training load)|

## License

AGPLv3 — see [LICENCE](LICENCE).

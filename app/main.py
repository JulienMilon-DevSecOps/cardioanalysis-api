"""FastAPI application factory.

Creates the FastAPI instance, registers all routers, and manages
the application lifespan (database connection open and close).
"""

import logging
import time

from fastapi import FastAPI, Request

from app.routers import health_router

# ── Routers ───────────────────────────────────────────────────────────────────
# from app.routers import parse_router, analyze_router, sessions_router, analytics_router

logger = logging.getLogger(__name__)

description = """
REST API exposing the **cardiolab** HRV analysis toolkit as an HTTP service.

Designed for cardiac rhythm monitoring and performance tracking — from resting HRV
to advanced physiological protocols and training load management.

## Protocols
- **Resting HRV** — time-domain, frequency-domain and non-linear features (RMSSD, LF/HF, DFA α1, SampEn…)
- **Orthostatic** — supine vs. standing autonomic response, readiness and autonomic scores
- **Cardiac coherence** — coherence score, resonance frequency analysis
- **Heart Rate Recovery (HRR)** — post-exercise recovery kinetics
- **Cardiac drift** — HRV stability during prolonged effort
- **VO2max** — aerobic capacity estimation from HRV

## Analytics
- **Readiness scoring** — baseline-relative score (0–100) integrating multiple HRV metrics
- **Training load** — ATL, CTL, TSB and TRIMP modelling (Banister & HRV-based)

## Data import
Polar (.txt/.csv/.rr), HRV4Training (.csv), Apple Health (.xml), Garmin (.fit/.csv)

## Scientific foundation
All computations are performed by [cardiolab](https://gitlab.com/project-jmilon-devsecops/cardioanalysis/cardiolab),
the scientific core of the cardioanalysis platform.
References: Task Force 1996, Shaffer & Ginsberg 2017.
"""

app = FastAPI(
    title="cardioanalysis-api",
    summary="HRV analysis REST API for cardiac monitoring and athlete performance tracking.",
    description=description,
    version="0.1.0",
    contact={
        "name": "Julien MILON",
        "email": "julienmilon.devsecops@gmail.com",
        # "url": ""
    },
    license_info={
        "name": "AGPL-3.0",
        "url": "https://www.gnu.org/licenses/agpl-3.0.en.html",
    },
)

app.include_router(health_router)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Measure and attach request processing time to the response headers."""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    logger.debug("Request processed in %.4f seconds", process_time)
    response.headers["X-Process-Time"] = str(process_time)
    return response

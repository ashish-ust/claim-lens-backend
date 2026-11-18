"""FastAPI application entry point. Run from the project root: uvicorn app.main:app --reload --port 8000"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import init_db
from app.routers import analyses, claims
from app.schemas import HealthResponse


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Create the data folders and tables on startup."""
    init_db()
    yield


app = FastAPI(title="ClaimLens API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyses.router)
app.include_router(claims.router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return FastAPI's field errors as one readable {"detail": "..."} string."""
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err.get("loc", ())[1:]) or "request"
        parts.append(f"{field}: {err.get('msg', 'invalid')}")
    return JSONResponse(status_code=422, content={"detail": "; ".join(parts) or "Invalid request"})


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness check and whether MOCK_MODE is on."""
    return HealthResponse(status="ok", mock_mode=get_settings().mock_mode)

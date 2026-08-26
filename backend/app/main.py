"""
uvicorn app.main:app --reload --port 8000   (run from backend/)

CORS is open to the Vite dev origins as belt-and-braces; in normal dev
the Vite proxy forwards /api so the browser never crosses origins.
"""

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import recon
from db import init_db
from logging_setup import configure_logging, customer_id_var, get_logger, request_id_var

from .routes import router

configure_logging()
request_logger = get_logger("app.request")
error_logger = get_logger("app.errors")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # bring any blank database (SQLite file or RDS) to the current schema
    # and seed the default customer; idempotent on every start
    init_db()
    # init_db() now tells alembic to skip its fileConfig() entirely
    # (configure_logger=False), so this re-call is belt-and-braces
    # against any OTHER library that reconfigures root logging; harmless
    # and idempotent
    configure_logging()
    yield


app = FastAPI(title="Recon Engine API", version=recon.__version__,
              lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Catches every request, including 404s and malformed bodies that
    never reach a route handler.

    customer_id for this summary line comes from request.state, not the
    customer_id_var ContextVar — verified empirically that Starlette's
    BaseHTTPMiddleware runs call_next's inner app in its own spawned task,
    so a ContextVar.set() made inside the route handler (a POST body
    field, resolved after this middleware already ran) never flows back
    out to this outer scope once call_next() returns; contextvars only
    propagate forward into new tasks, never backward out of them.
    request.state IS the same object across that boundary, so route
    handlers that resolve a customer set request.state.customer_id
    directly. Domain-event log lines emitted from db/ code deep inside
    that same request are unaffected by this — they run within the
    forward-propagated child context, where customer_id_var reads fine."""
    request_id_var.set(request.headers.get("x-request-id", uuid.uuid4().hex[:12]))
    if "customer_id" in request.query_params:
        customer_id_var.set(request.query_params["customer_id"])

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    request_logger.info("http.request", extra={
        "event_type": "http.request",
        "details": {"method": request.method, "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                    "customer_id": getattr(request.state, "customer_id", None)}})
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Anything that escapes every try/except already in routes.py lands
    here instead of crashing to a raw, unlogged traceback. Confirmed this
    does not shadow FastAPI's own HTTPException/RequestValidationError
    handling — those keep their existing specific responses."""
    error_logger.exception("http.unhandled_exception", extra={
        "event_type": "http.unhandled_exception",
        "details": {"method": request.method, "path": request.url.path}})
    return JSONResponse(status_code=500,
                        content={"error": "INTERNAL_ERROR",
                                 "detail": f"{type(exc).__name__}: {exc}"})


app.include_router(router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": recon.__version__}

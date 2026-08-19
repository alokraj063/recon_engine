"""
uvicorn app.main:app --reload --port 8000   (run from backend/)

CORS is open to the Vite dev origins as belt-and-braces; in normal dev
the Vite proxy forwards /api so the browser never crosses origins.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import recon

from .routes import router

app = FastAPI(title="Recon Engine API", version=recon.__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": recon.__version__}

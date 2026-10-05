"""Standalone ECM gateway. From backend/ECM/:

    ../../.venv/bin/uvicorn ecm_api.main:app --port 8100

Binds to 127.0.0.1 by default: this app has NO login gate of its own. When it joins the recon
backend, mount ecm_api.routes.router under that app's authenticated /api router instead.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="ECM gateway", version="0.1.0")
app.include_router(router)

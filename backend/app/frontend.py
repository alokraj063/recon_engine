"""
Serve the built React SPA (frontend/dist) from the API process, so one
container answers both / (the UI) and /api/* (the API) on one origin — no
CORS, no second service, and the frontend's relative '/api/...' calls work
unchanged.

The SPA routes with the URL hash (App.tsx: location.hash / hashchange), so
the server only ever sees '/' plus real asset paths — no history-API
catch-all is needed, and an unknown path is an honest 404.

FRONTEND_DIST overrides where the build is read from (the Docker image
copies it next to the backend); default is <repo>/frontend/dist. No build
there -> nothing is mounted and the API runs alone, as in local dev where
Vite serves the UI on :5173.

Caching: Vite fingerprints everything under assets/ (content hash in the
file name), so those are cached for a year; index.html is revalidated on
every load so a new deploy is picked up immediately.
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

REPO_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DIST = REPO_DIR / "frontend" / "dist"

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
SHORT = "public, max-age=3600"


def frontend_dist() -> Path:
    return Path(os.environ.get("FRONTEND_DIST") or DEFAULT_DIST)


class SPAStaticFiles(StaticFiles):
    """StaticFiles with cache headers suited to a fingerprinted Vite build."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        # decide by the file actually served (a request for "/" serves
        # index.html), relative to the build directory
        try:
            rel = (Path(full_path).resolve()
                   .relative_to(Path(self.directory).resolve()).as_posix())
        except ValueError:
            rel = Path(full_path).name
        if rel.startswith("assets/"):
            response.headers["Cache-Control"] = IMMUTABLE
        elif rel == "index.html":
            response.headers["Cache-Control"] = REVALIDATE
        else:
            response.headers["Cache-Control"] = SHORT
        return response


def mount_frontend(app: FastAPI, dist: Optional[Path] = None) -> bool:
    """Mount the SPA at '/'. Must be called AFTER every API route is
    registered: a mount at '/' matches any path, and routes are tried in
    registration order. Returns whether a build was found and mounted."""
    dist = Path(dist) if dist is not None else frontend_dist()
    if not (dist / "index.html").is_file():
        return False
    app.mount("/", SPAStaticFiles(directory=dist, html=True), name="frontend")
    return True

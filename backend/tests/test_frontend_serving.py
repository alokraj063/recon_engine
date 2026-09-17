"""
The built SPA is served from the API process at '/', without shadowing
any /api route, with cache headers suited to Vite's fingerprinted assets.
Uses a fake dist folder — no Node build needed.
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.frontend import (IMMUTABLE, REVALIDATE, SHORT,  # noqa: E402
                          frontend_dist, mount_frontend)


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><div id=root></div>")
    (d / "assets" / "index-Ab12Cd.js").write_text("console.log('app')")
    (d / "logo.png").write_bytes(b"\x89PNG")
    return d


def _app(dist):
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    mounted = mount_frontend(app, dist)
    return app, mounted


def test_serves_index_assets_and_keeps_api_routes(dist):
    app, mounted = _app(dist)
    assert mounted
    client = TestClient(app)

    root = client.get("/")
    assert root.status_code == 200 and "id=root" in root.text
    assert root.headers["cache-control"] == REVALIDATE
    assert client.get("/index.html").headers["cache-control"] == REVALIDATE

    asset = client.get("/assets/index-Ab12Cd.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == IMMUTABLE

    assert client.get("/logo.png").headers["cache-control"] == SHORT

    # the API route registered before the mount still wins
    api = client.get("/api/health")
    assert api.status_code == 200 and api.json() == {"status": "ok"}


def test_unknown_paths_are_404_not_index(dist):
    """Hash routing: no history-API fallback, so a bad path is a real 404
    (an unknown /api path must not come back as HTML with status 200)."""
    client = TestClient(_app(dist)[0])
    assert client.get("/no/such/page").status_code == 404
    assert client.get("/api/does-not-exist").status_code == 404


def test_no_build_means_api_only(tmp_path):
    app, mounted = _app(tmp_path / "missing")
    assert not mounted
    client = TestClient(app)
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200


def test_frontend_dist_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))
    assert frontend_dist() == tmp_path
    monkeypatch.delenv("FRONTEND_DIST")
    assert frontend_dist().parts[-2:] == ("frontend", "dist")

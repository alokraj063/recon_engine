import pytest
from fastapi.testclient import TestClient

from ecm_api.main import app
from ecm_api.routes import get_service
from tests.fake_nuxeo import FakeNuxeo
from tests.test_ecm_client import make


@pytest.fixture
def ctx():
    fake = FakeNuxeo()
    svc = make(fake)
    app.dependency_overrides[get_service] = lambda: svc
    yield TestClient(app), fake
    app.dependency_overrides.clear()
    fake.close()


def test_catalog_lists_sites_and_kinds(ctx):
    client, _ = ctx
    body = client.get("/api/ecm/catalog").json()
    assert {s["key"] for s in body["sites"]} == {"hosur", "rohtak"}
    kinds = {k["code"]: k for k in body["kinds"]}
    assert kinds["rNoteReports"]["upload"]["required"] == ["invoice_number"]
    assert kinds["loi"]["locations"] == ["tenders"]
    assert kinds["taxInvoice"]["upload"] is None and kinds["taxInvoice"]["filters"] == {"is_signed": True}


def test_upload_search_get_download(ctx):
    client, fake = ctx
    r = client.post("/api/ecm/documents",
                    data={"site": "hosur", "kind": "crnReports", "invoice_number": "INV-7"},
                    files={"file": ("crn 7.pdf", b"%PDF-7", "application/pdf")})
    assert r.status_code == 201, r.text
    uid = r.json()["uid"]
    assert fake.created[-1]["properties"]["InvoiceCertificateSchema:invoiceNumber"] == "INV-7"

    listed = client.get("/api/ecm/documents", params={"site": "hosur", "kind": "crnReports"}).json()
    assert listed["count"] == 1 and listed["documents"][0]["uid"] == uid

    assert client.get(f"/api/ecm/documents/{uid}").json()["metadata"] == {"invoice_number": "INV-7"}

    filtered = client.get("/api/ecm/documents",
                          params={"site": "hosur", "kind": "crnReports", "invoice_number": "INV-7"})
    assert filtered.status_code == 200
    assert "InvoiceCertificateSchema:invoiceNumber = 'INV-7'" in fake.queries[-1]

    dl = client.get(f"/api/ecm/documents/{uid}/content")
    assert dl.content == b"%PDF-7" and dl.headers["content-type"] == "application/pdf"
    assert "crn%207.pdf" in dl.headers["content-disposition"]


def test_missing_invoice_number_is_400(ctx):
    client, fake = ctx
    r = client.post("/api/ecm/documents", data={"site": "hosur", "kind": "rejectionAdvise"},
                    files={"file": ("a.pdf", b"x", "application/pdf")})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "INVALID_INPUT"
    assert fake.calls == []


def test_unknown_document_is_404(ctx):
    client, _ = ctx
    assert client.get("/api/ecm/documents/nope").status_code == 404
    assert client.get("/api/ecm/documents/nope/content").status_code == 404


def test_health_reports_cache_without_token(ctx):
    client, fake = ctx
    client.get("/api/ecm/documents", params={"site": "hosur", "kind": "billStatus"})
    h = client.get("/api/ecm/health").json()
    assert h["token"]["cached"] is True and h["token"]["fetch_count"] == 1
    assert "tok-1" not in str(h)


def test_unknown_filter_is_400(ctx):
    client, fake = ctx
    r = client.get("/api/ecm/documents", params={"site": "hosur", "kind": "billStatus", "colour": "red"})
    assert r.status_code == 400 and "unknown field" in r.json()["detail"]["detail"]
    assert fake.calls == []

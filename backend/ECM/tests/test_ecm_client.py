import json

import pytest

from ecm_client import ECMClient, ECMDocumentService, ECMSettings
from ecm_client.auth import TokenProvider
from ecm_client.errors import ECMConfigError, ECMNotFound, ECMValidationError
from ecm_client.transport import UrllibTransport
from tests.fake_nuxeo import FakeNuxeo

HOSUR_TEST_ID = "ad5090e5-648d-4f75-8c14-7052e8ac3b72"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def fake():
    f = FakeNuxeo()
    yield f
    f.close()


def make(fake, secret="sec", clock=None, env="TEST", config_dir=None):
    s = ECMSettings(domain=fake.url + "/", client_id="cid", client_secret=secret,
                    environment=env, config_dir=config_dir)
    t = UrllibTransport(timeout=5)
    tokens = TokenProvider(s, t, clock=clock) if clock else None
    return ECMDocumentService(ECMClient(s, transport=t, token_provider=tokens))


def entry(uid, kind, filename="f.pdf", **props):
    return {"uid": uid, "type": "InvoiceCertificate", "path": f"/x/{uid}", "title": uid,
            "properties": {"InvoiceCertificateSchema:type": kind,
                           "file:content": {"name": filename, "length": "3"}, **props}}


# ---------------------------------------------------------------- token cache
def test_token_is_reused_within_the_hour_and_refetched_after(fake):
    clock = Clock()
    svc = make(fake, clock=clock)
    for _ in range(3):
        svc.find_documents(site="hosur", kind="billStatus")
    assert fake.tokens_issued == ["tok-1"]

    clock.now += 3600 - 60 - 1          # just inside expires_in minus the refresh margin
    svc.find_documents(site="hosur", kind="billStatus")
    assert len(fake.tokens_issued) == 1

    clock.now += 2                      # past it: one new fetch
    svc.find_documents(site="hosur", kind="billStatus")
    assert fake.tokens_issued == ["tok-1", "tok-2"]
    assert svc.client.tokens.fetch_count == 2


def test_token_ttl_falls_back_to_an_hour_without_expires_in(fake):
    fake.expires_in = None
    clock = Clock()
    svc = make(fake, clock=clock)
    svc.client.tokens.get()
    clock.now += 3500
    svc.client.tokens.get()
    assert len(fake.tokens_issued) == 1
    clock.now += 100
    svc.client.tokens.get()
    assert len(fake.tokens_issued) == 2


def test_revoked_token_is_replaced_once_on_401(fake):
    svc = make(fake)
    svc.find_documents(site="hosur", kind="billStatus")
    fake.revoked.add("tok-1")
    svc.find_documents(site="hosur", kind="billStatus")
    assert fake.tokens_issued == ["tok-1", "tok-2"]


def test_bad_credentials_fail_without_leaking_the_secret(fake):
    svc = make(fake, secret="wrong-secret")
    with pytest.raises(Exception) as err:
        svc.client.tokens.get()
    assert "wrong-secret" not in str(err.value)


def test_health_never_contains_the_token(fake):
    svc = make(fake)
    svc.client.tokens.get()
    h = svc.health()
    assert h["token"]["cached"] is True and h["token"]["seconds_remaining"] > 3000
    assert "tok-1" not in repr(h) and "sec" not in repr(svc.client.settings)


def test_missing_settings_raise_config_error():
    with pytest.raises(ECMConfigError):
        ECMSettings(domain="", client_id="x", client_secret="y")


# ---------------------------------------------------------------- queries from config
def test_query_matches_oceane_spreadsheet_shape(fake):
    q = make(fake, env="PROD").build_query(site="hosur", kind="taxInvoice")
    assert "InvoiceCertificateSchema:type = 'taxInvoice'" in q
    assert "InvoiceCertificateSchema:isSigned = 1" in q                      # kind filter from config
    assert "ecm:ancestorId = '7dfe3fc0-3157-489d-826a-9435e65631fc'" in q   # PROD Hosur id
    assert "ecm:path" not in q                                              # no location asked for


def test_test_environment_uses_test_site_ids(fake):
    q = make(fake).build_query(site="hosur", kind="billStatus")
    assert f"ecm:ancestorId = '{HOSUR_TEST_ID}'" in q


def test_storage_location_fills_invoice_number_and_does_not_repeat_it(fake):
    q = make(fake, env="PROD").build_query(site="rohtak", kind="inspectionCertificate",
                                           location="storage", filters={"invoice_number": "INV-9"})
    assert "ecm:path STARTSWITH '/FT/workspaces/Sites/HPD/Invoice Certificate/Storage/INV-9'" in q
    assert "InvoiceCertificateSchema:invoiceNumber" not in q


def test_storage_without_invoice_number_searches_the_whole_storage_folder(fake):
    q = make(fake, env="PROD").build_query(site="hosur", kind="workCertificate", location="storage")
    assert "ecm:path STARTSWITH '/FT/workspaces/Sites/HSR/Invoice Certificate/Storage'" in q


def test_field_filters_quote_escape_and_handle_lists(fake):
    svc = make(fake)
    q = svc.build_query(site="hosur", kind="rNoteReports", filters={"invoice_number": "IN'V"})
    assert "InvoiceCertificateSchema:invoiceNumber = 'IN\\'V'" in q
    q = svc.build_query(site="hosur", kind="poCopy", filters={"customer_id": "CU1"})
    assert "CustomerCommonSchema:customerId/* = 'CU1'" in q


def test_search_without_kind_covers_every_kind_in_the_site(fake):
    q = make(fake).build_query(site="rohtak")
    assert "ecm:primaryType = 'InvoiceCertificate'" in q and "InvoiceCertificateSchema:type" not in q


@pytest.mark.parametrize("kw, message", [
    (dict(site="pune", kind="billStatus"), "unknown site"),
    (dict(site="hosur", kind="airWayBill"), "unknown kind"),
    (dict(site="hosur", kind="billStatus", filters={"colour": "red"}), "unknown field"),
    (dict(site="hosur", kind="poCopy", filters={"invoice_number": "1"}), "cannot be filtered by"),
    (dict(site="hosur", kind="billStatus", location="tenders"), "does not live in"),
    (dict(site="rohtak", kind="loi", location="tenders"), "has no 'tenders' location"),
    (dict(site="hosur", kind="taxInvoice", filters={"is_signed": "maybe"}), "true/false"),
])
def test_bad_search_is_rejected_before_any_ecm_call(fake, kw, message):
    with pytest.raises(ECMValidationError, match=message):
        make(fake).find_documents(**kw)
    assert fake.calls == []


def test_search_follows_pages_and_respects_limit(fake):
    fake.search_results = [entry(f"d{i:03d}", "billStatus") for i in range(250)]
    svc = make(fake)
    assert len(svc.find_documents(site="hosur", kind="billStatus", limit=1000)) == 250
    assert len(svc.find_documents(site="hosur", kind="billStatus", limit=120)) == 120


def test_summary_carries_configured_metadata(fake):
    fake.search_results = [entry("u1", "inspectionCertificate", **{
        "InvoiceCertificateSchema:invoiceNumber": "INV-1", "InvoiceCertificateSchema:icNumber": "IC-5",
        "InvoiceCertificateSchema:coNumber": "", "unrelated:prop": "x"})]
    doc = make(fake).find_documents(site="hosur", kind="inspectionCertificate")[0]
    assert doc.kind == "inspectionCertificate"
    assert doc.metadata == {"invoice_number": "INV-1", "ic_number": "IC-5"}


# ---------------------------------------------------------------- download
def test_get_and_download_round_trip(fake, tmp_path):
    svc = make(fake)
    saved = svc.save_document(site="hosur", kind="billStatus", content=b"PK-data",
                              filename="Bill Status 05-10.xlsx")
    assert svc.get(saved.uid).filename == "Bill Status 05-10.xlsx"
    blob = svc.download(saved.uid)
    assert blob.data == b"PK-data" and blob.filename == "Bill Status 05-10.xlsx"
    out = svc.download_to(saved.uid, tmp_path / "dl")
    assert out == tmp_path / "dl" / "Bill Status 05-10.xlsx" and out.read_bytes() == b"PK-data"
    assert not list((tmp_path / "dl").glob("*.part"))


def test_download_of_missing_document_leaves_no_partial_file(fake, tmp_path):
    with pytest.raises(ECMNotFound):
        make(fake).download_to("missing", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_pull_downloads_into_site_kind_folders_with_manifest(fake, tmp_path):
    svc = make(fake)
    a = svc.save_document(site="hosur", kind="rNoteReports", content=b"AAA", filename="rn.pdf",
                          fields={"invoice_number": "INV-1"})
    b = svc.save_document(site="hosur", kind="rNoteReports", content=b"BB", filename="rn.pdf",
                          fields={"invoice_number": "INV-2"})

    results = svc.pull(site="hosur", kind="rNoteReports", dest_dir=tmp_path)
    assert [r.status for r in results] == ["downloaded", "downloaded"]
    folder = tmp_path / "hosur" / "rNoteReports"
    names = sorted(p.name for p in folder.iterdir())
    assert names == sorted(["manifest.json", f"rn__{a.uid[:8]}.pdf", f"rn__{b.uid[:8]}.pdf"])
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest[a.uid]["metadata"] == {"invoice_number": "INV-1"}
    assert manifest[a.uid]["local_file"] == f"rn__{a.uid[:8]}.pdf"

    again = svc.pull(site="hosur", kind="rNoteReports", dest_dir=tmp_path)
    assert [r.status for r in again] == ["skipped", "skipped"]
    assert [r.status for r in svc.pull(site="hosur", kind="rNoteReports", dest_dir=tmp_path,
                                       overwrite=True)] == ["downloaded", "downloaded"]


def test_pull_reports_a_failed_file_and_keeps_going(fake, tmp_path):
    svc = make(fake)
    ok = svc.save_document(site="hosur", kind="billStatus", content=b"x", filename="b.xlsx")
    fake.search_results = [entry("no-blob-doc", "billStatus"), fake.docs[ok.uid]]
    results = svc.pull(site="hosur", kind="billStatus", dest_dir=tmp_path)
    assert [r.status for r in results] == ["failed", "downloaded"]
    assert "404" in results[0].error
    manifest = json.loads((tmp_path / "hosur" / "billStatus" / "manifest.json").read_text())
    assert list(manifest) == [ok.uid]


# ---------------------------------------------------------------- upload (unchanged behaviour)
def test_upload_sends_type_invoice_and_attachment(fake):
    svc = make(fake)
    doc = svc.save_document(site="hosur", kind="rNoteReports", content=b"%PDF-1",
                            filename="r note 1.pdf", invoice_number=" INV-1 ")
    sent = fake.created[-1]
    assert sent["folder"] == "folder-hsr-inputs" and sent["type"] == "InvoiceCertificate"
    assert sent["properties"]["InvoiceCertificateSchema:type"] == "rNoteReports"
    assert sent["properties"]["InvoiceCertificateSchema:invoiceNumber"] == "INV-1"
    assert sent["properties"]["file:content"] == {"upload-batch": "batch-1", "upload-fileId": "0"}
    assert doc.kind == "rNoteReports" and doc.size == 6


def test_folder_id_is_resolved_once_per_path(fake):
    svc = make(fake)
    for i in range(3):
        svc.save_document(site="hosur", kind="billStatus", content=b"x", filename=f"b{i}.xlsx")
    assert len([q for q in fake.queries if "ecm:path = " in q]) == 1


def test_tender_upload_goes_to_tenders_with_number_and_date(fake):
    make(fake).save_document(site="hosur", kind="loi", content=b"x", filename="loi.pdf",
                             tender_number="T-9", tender_date="2026-10-01")
    sent = fake.created[-1]
    assert sent["folder"] == "folder-hsr-tenders"
    assert sent["properties"]["InvoiceCertificateSchema:tenderDate"] == "2026-10-01"


@pytest.mark.parametrize("kw, message", [
    (dict(site="hosur", kind="crnReports"), "requires invoice_number"),
    (dict(site="hosur", kind="vft", tender_number="T"), "requires tender_date"),
    (dict(site="hosur", kind="co", tender_number="T", tender_date="01/10/2026"), "YYYY-MM-DD"),
    (dict(site="rohtak", kind="loi", tender_number="T", tender_date="2026-10-01"), "no tenders location"),
    (dict(site="hosur", kind="taxInvoice"), "download-only"),
])
def test_bad_upload_is_rejected_before_any_ecm_call(fake, kw, message):
    with pytest.raises(ECMValidationError, match=message):
        make(fake).save_document(content=b"x", filename="f.pdf", **kw)
    assert fake.calls == []


# ---------------------------------------------------------------- config loading
def _write_config(tmp_path, kinds: str, sites: str, env="test"):
    (tmp_path / "document_kinds.toml").write_text(kinds)
    (tmp_path / f"sites.{env}.toml").write_text(sites)
    return tmp_path


GOOD_KINDS = '''
doc_type = "InvoiceCertificate"
type_property = "InvoiceCertificateSchema:type"
[fields]
invoice_number = "InvoiceCertificateSchema:invoiceNumber"
[kinds.newReport]
label = "New report"
locations = ["inbox"]
fields = ["invoice_number"]
'''
GOOD_SITES = '''
[sites.pune]
name = "Pune"
site_id = "pune-site-id"
[sites.pune.locations]
inbox = "/FT/workspaces/Sites/PUN/Inbox/{invoice_number}"
'''


def test_new_kind_and_site_need_only_config(fake, tmp_path):
    cfg = _write_config(tmp_path, GOOD_KINDS, GOOD_SITES)
    q = make(fake, config_dir=str(cfg)).build_query(site="pune", kind="newReport", location="inbox",
                                                    filters={"invoice_number": "7"})
    assert "InvoiceCertificateSchema:type = 'newReport'" in q
    assert "ecm:ancestorId = 'pune-site-id'" in q
    assert "ecm:path STARTSWITH '/FT/workspaces/Sites/PUN/Inbox/7'" in q


def test_every_config_mistake_is_reported_together(fake, tmp_path):
    kinds = GOOD_KINDS + '''
[kinds.broken]
locations = []
fields = ["nope"]
filters = { also_nope = 1 }
upload = { location = "elsewhere", required = ["invoice_number"] }
'''
    sites = GOOD_SITES.replace("/FT/workspaces/Sites/PUN/Inbox", "/FT/workspaces/Sites/PUN%20X/Inbox") \
                      .replace("{invoice_number}", "{po}")
    cfg = _write_config(tmp_path, kinds, sites)
    with pytest.raises(ECMConfigError) as err:
        make(fake, config_dir=str(cfg))
    msg = str(err.value)
    for expected in ["locations must be a non-empty list", "field 'nope'", "filter field 'also_nope'",
                     "upload.location 'elsewhere'", "upload requires 'invoice_number'",
                     "URL-encoded", "placeholder {po}"]:
        assert expected in msg, expected


def test_toml_syntax_error_names_the_file(fake, tmp_path):
    cfg = _write_config(tmp_path, GOOD_KINDS + "\n[kinds.x\n", GOOD_SITES)
    with pytest.raises(ECMConfigError, match="document_kinds.toml"):
        make(fake, config_dir=str(cfg))


def test_shipped_config_loads_for_both_environments(fake):
    for env in ("TEST", "PROD"):
        assert len(make(fake, env=env).catalog.kinds) >= 18

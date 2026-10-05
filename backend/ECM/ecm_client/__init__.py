"""ECM (Nuxeo) client for invoice-certificate documents.

    from ecm_client import ECMDocumentService
    svc = ECMDocumentService.from_env()          # ECM_* env / ECM/.env + config/*.toml
    docs = svc.find_documents(site="hosur", kind="taxInvoice", filters={"invoice_number": "INV-1"})
    svc.download_to(docs[0].uid, "downloads/")
    svc.pull(site="hosur", kind="inspectionCertificate", dest_dir="downloads/")

Layers: transport (HTTP) -> auth (cached bearer token) -> client (Nuxeo endpoints)
-> service (sites, kinds, fields from catalog config). Standalone: imports nothing from
the recon engine.
"""
from .catalog import Catalog, DocumentKind, Field, Site, load_catalog
from .client import Blob, ECMClient, nxql_literal
from .config import ECMSettings
from .errors import ECMAuthError, ECMConfigError, ECMError, ECMNotFound, ECMValidationError
from .service import DocumentSummary, ECMDocumentService, PulledFile

__all__ = [
    "Blob", "Catalog", "DocumentKind", "DocumentSummary", "ECMAuthError", "ECMClient",
    "ECMConfigError", "ECMDocumentService", "ECMError", "ECMNotFound", "ECMSettings",
    "ECMValidationError", "Field", "PulledFile", "Site", "load_catalog", "nxql_literal",
]

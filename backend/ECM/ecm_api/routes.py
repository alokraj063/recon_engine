"""/api/ecm routes. Mount on any FastAPI app:

    from ecm_api.routes import router as ecm_router
    gated_api_router.include_router(ecm_router)     # inherits that router's auth dependency

Errors use the recon app's shape: {"detail": {"error": CODE, "detail": message}}.
"""
from __future__ import annotations

import urllib.parse
from functools import lru_cache
from typing import Annotated

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query, Request, Response,
                     UploadFile, status)

from ecm_client.errors import (ECMAuthError, ECMConfigError, ECMError, ECMNotFound,
                               ECMValidationError)
from ecm_client.service import ECMDocumentService

router = APIRouter(prefix="/api/ecm", tags=["ecm"])

MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@lru_cache(maxsize=1)
def _service() -> ECMDocumentService:
    # one client per process, so the bearer token cache is shared by every request
    return ECMDocumentService.from_env()


def get_service() -> ECMDocumentService:
    try:
        return _service()
    except ECMConfigError as exc:
        raise HTTPException(500, detail={"error": "ECM_NOT_CONFIGURED", "detail": str(exc)}) from exc


Service = Annotated[ECMDocumentService, Depends(get_service)]


def _raise(exc: ECMError):
    if isinstance(exc, ECMValidationError):
        code, http = "INVALID_INPUT", status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, ECMNotFound):
        code, http = "NOT_FOUND", status.HTTP_404_NOT_FOUND
    elif isinstance(exc, ECMAuthError):
        code, http = "ECM_AUTH_FAILED", status.HTTP_502_BAD_GATEWAY
    elif isinstance(exc, ECMConfigError):
        code, http = "ECM_NOT_CONFIGURED", status.HTTP_500_INTERNAL_SERVER_ERROR
    else:
        code, http = "ECM_UPSTREAM_ERROR", status.HTTP_502_BAD_GATEWAY
    raise HTTPException(http, detail={"error": code, "detail": str(exc)}) from exc


@router.get("/health")
def health(svc: Service):
    """Settings in use and the token cache state. Does not call the ECM."""
    return svc.health()


@router.get("/catalog")
def get_catalog(svc: Service):
    """Sites, locations, kinds and fields from the config files."""
    return svc.catalog.as_dict()


_SEARCH_PARAMS = {"site", "kind", "location", "limit"}


@router.get("/documents")
def list_documents(request: Request, svc: Service, site: str, kind: str | None = None,
                   location: str | None = None, limit: int = Query(200, ge=1, le=2000)):
    """Search. Any other query parameter is a field filter, e.g. ?invoice_number=INV-1
    (field keys are listed by /catalog; an unknown one is a 400)."""
    filters = {k: v for k, v in request.query_params.items() if k not in _SEARCH_PARAMS}
    try:
        docs = svc.find_documents(site=site, kind=kind, location=location, filters=filters, limit=limit)
    except ECMError as exc:
        _raise(exc)
    return {"count": len(docs), "documents": [d.to_dict() for d in docs]}


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def create_document(svc: Service, site: Annotated[str, Form()], kind: Annotated[str, Form()],
                          file: Annotated[UploadFile, File()],
                          title: Annotated[str | None, Form()] = None,
                          invoice_number: Annotated[str | None, Form()] = None,
                          tender_number: Annotated[str | None, Form()] = None,
                          tender_date: Annotated[str | None, Form()] = None):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail={"error": "FILE_TOO_LARGE",
                                         "detail": f"limit is {MAX_UPLOAD_BYTES} bytes"})
    try:
        doc = svc.save_document(site=site, kind=kind, content=content,
                                filename=file.filename or "upload", title=title,
                                invoice_number=invoice_number, tender_number=tender_number,
                                tender_date=tender_date, mime_type=file.content_type)
    except ECMError as exc:
        _raise(exc)
    return doc.to_dict()


@router.get("/documents/{uid}")
def get_document(svc: Service, uid: str):
    try:
        return svc.get(uid).to_dict()
    except ECMError as exc:
        _raise(exc)


@router.get("/documents/{uid}/content")
def download_document(svc: Service, uid: str):
    try:
        blob = svc.download(uid)
    except ECMError as exc:
        _raise(exc)
    quoted = urllib.parse.quote(blob.filename)
    return Response(content=blob.data, media_type=blob.content_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})

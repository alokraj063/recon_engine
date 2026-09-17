"""
Managed file storage: bronze source files (content-addressed by sha256 per
customer) and run artifacts (the Recon_Output.xlsx workbook per run).

Callers hold REFERENCES, persisted as plain strings in
bronze.files.stored_path and runs.workbook_path:
  - a local filesystem path            data/bronze/{customer}/{sha}.pdf
  - an S3 URI                          s3://{bucket}/{prefix}bronze/{customer}/{sha}.pdf

WHERE new files are written is configuration (STORAGE_BACKEND); HOW a
reference is read is decided by the reference itself. So a database whose
older rows point at local paths and newer rows at S3 reads both, and
switching backends never needs a data migration for existing references.

Settings (env or backend/.env):
  STORAGE_BACKEND       local (default) | s3
  S3_BRONZE_BUCKET      bucket for uploaded source files      (s3 only)
  S3_RUNS_BUCKET        bucket for run workbooks; defaults to S3_BRONZE_BUCKET
  S3_PREFIX             optional key prefix, e.g. "recon/"
  S3_KMS_KEY_ID         optional: request SSE-KMS with this key explicitly.
                        Unset = rely on the bucket's default encryption.
  S3_ENDPOINT_URL       optional: MinIO / LocalStack for local testing
  AWS_REGION            region for the S3 client (ECS sets credentials via
                        the task role; nothing secret lives in settings)

IAM for the task role: s3:GetObject + s3:PutObject on both buckets' objects,
AND s3:ListBucket on the buckets. Without ListBucket, S3 answers a HEAD on a
missing key with 403 instead of 404, so the bronze "already stored?" check
cannot tell "new file" from "forbidden" and every new upload fails. With
S3_KMS_KEY_ID or SSE-KMS bucket defaults, also kms:GenerateDataKey and
kms:Decrypt on that key.

Retention: local run workbooks beyond the newest 20 are pruned in-process.
On S3 that is an S3 lifecycle rule on the runs prefix instead — listing
and deleting objects from every request would be the wrong tool.
"""

import hashlib
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple

from .base import DATA_DIR

S3_SCHEME = "s3://"
RUN_WORKBOOK_NAME = "Recon_Output.xlsx"


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_s3_ref(ref: str) -> bool:
    return str(ref).startswith(S3_SCHEME)


def parse_s3_ref(ref: str) -> Tuple[str, str]:
    """'s3://bucket/some/key.pdf' -> ('bucket', 'some/key.pdf')."""
    if not is_s3_ref(ref):
        raise ValueError(f"not an S3 reference: {ref!r}")
    bucket, _, key = str(ref)[len(S3_SCHEME):].partition("/")
    if not bucket or not key:
        raise ValueError(f"malformed S3 reference: {ref!r}")
    return bucket, key


def _s3_client(endpoint_url: Optional[str] = None):
    import boto3
    return boto3.client(
        "s3",
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
        endpoint_url=endpoint_url or os.environ.get("S3_ENDPOINT_URL") or None,
    )


def _is_not_found(exc) -> bool:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound")


class LocalBackend:
    """data/bronze/{customer_key}/{sha256}{ext} and data/runs/{run_id}/."""

    name = "local"

    def __init__(self, root: Path = DATA_DIR):
        self.root = Path(root)

    def save_bronze(self, customer_key: str, src: Path, sha256: str) -> str:
        dest = self.root / "bronze" / customer_key / f"{sha256}{Path(src).suffix.lower()}"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        return str(dest)

    def save_run_workbook(self, run_id: str, src: Path) -> str:
        dest = self.root / "runs" / run_id / RUN_WORKBOOK_NAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return str(dest)

    def prune_run_files(self, keep: int = 20) -> None:
        """File retention only — DB rows are kept (they are the audit
        trail). Drops the oldest run directories beyond `keep`."""
        runs_dir = self.root / "runs"
        if not runs_dir.is_dir():
            return
        dirs = sorted((d for d in runs_dir.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime)
        if len(dirs) > keep:
            for d in dirs[:-keep]:
                shutil.rmtree(d, ignore_errors=True)


class S3Backend:
    """Same layout as LocalBackend, as object keys. Bronze keys are content-
    addressed, so an object that already exists is never re-uploaded."""

    name = "s3"

    def __init__(self, bronze_bucket: str, runs_bucket: Optional[str] = None,
                 prefix: str = "", kms_key_id: Optional[str] = None,
                 client=None):
        if not bronze_bucket:
            raise ValueError("S3 storage needs S3_BRONZE_BUCKET")
        self.bronze_bucket = bronze_bucket
        self.runs_bucket = runs_bucket or bronze_bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.kms_key_id = kms_key_id or None
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _s3_client()
        return self._client

    def _put(self, bucket: str, key: str, src: Path) -> str:
        extra = {}
        if self.kms_key_id:
            extra = {"ServerSideEncryption": "aws:kms",
                     "SSEKMSKeyId": self.kms_key_id}
        self.client.upload_file(str(src), bucket, key, ExtraArgs=extra or None)
        return f"{S3_SCHEME}{bucket}/{key}"

    def save_bronze(self, customer_key: str, src: Path, sha256: str) -> str:
        key = f"{self.prefix}bronze/{customer_key}/{sha256}{Path(src).suffix.lower()}"
        ref = f"{S3_SCHEME}{self.bronze_bucket}/{key}"
        if object_exists(self.client, self.bronze_bucket, key):
            return ref
        return self._put(self.bronze_bucket, key, src)

    def save_run_workbook(self, run_id: str, src: Path) -> str:
        key = f"{self.prefix}runs/{run_id}/{RUN_WORKBOOK_NAME}"
        return self._put(self.runs_bucket, key, src)

    def prune_run_files(self, keep: int = 20) -> None:
        """No-op: retention is an S3 lifecycle rule on {prefix}runs/."""


def object_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:  # botocore ClientError; imported lazily
        if _is_not_found(exc):
            return False
        raise


class Storage:
    """The one object callers use. Writes go to the configured backend;
    reads dispatch on the reference, so local and S3 refs coexist."""

    def __init__(self, backend):
        self.backend = backend
        self._reader_client = None

    # -- configuration --------------------------------------------------
    @property
    def root(self) -> Path:
        """Local data root (DATA_DIR). Meaningful for the local backend;
        kept on the facade for tests and local tooling."""
        return getattr(self.backend, "root", DATA_DIR)

    def use(self, backend) -> None:
        """Swap the write backend (tests; not used at runtime)."""
        self.backend = backend
        self._reader_client = None

    def _s3(self):
        client = getattr(self.backend, "client", None)
        if client is not None:
            return client
        if self._reader_client is None:
            self._reader_client = _s3_client()
        return self._reader_client

    # -- writes ---------------------------------------------------------
    def save_bronze(self, customer_key: str, src: Path, sha256: str) -> str:
        return self.backend.save_bronze(customer_key, Path(src), sha256)

    def save_run_workbook(self, run_id: str, src: Path) -> str:
        return self.backend.save_run_workbook(run_id, Path(src))

    def prune_run_files(self, keep: int = 20) -> None:
        self.backend.prune_run_files(keep)

    # -- reads ----------------------------------------------------------
    def exists(self, ref: Optional[str]) -> bool:
        if not ref:
            return False
        if is_s3_ref(ref):
            bucket, key = parse_s3_ref(ref)
            return object_exists(self._s3(), bucket, key)
        return Path(ref).is_file()

    def local_path(self, ref: str, workdir: Path) -> Path:
        """A real file for code that needs a path (pdfplumber, openpyxl).
        Local refs are returned as-is — never copied, never to be deleted
        by the caller. S3 refs are downloaded into `workdir`, which the
        caller owns and cleans up; the object's file name (sha256 + the
        original extension) is kept, since parsers dispatch on suffix."""
        if not is_s3_ref(ref):
            return Path(ref)
        bucket, key = parse_s3_ref(ref)
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        dest = workdir / Path(key).name
        self._s3().download_file(bucket, key, str(dest))
        return dest

    def download_to(self, ref: str, dest: Path) -> Path:
        """Copy the referenced file to exactly `dest` (any backend)."""
        dest = Path(dest)
        if is_s3_ref(ref):
            bucket, key = parse_s3_ref(ref)
            self._s3().download_file(bucket, key, str(dest))
        else:
            shutil.copyfile(ref, dest)
        return dest


def backend_from_env():
    kind = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
    if kind == "local":
        return LocalBackend()
    if kind == "s3":
        return S3Backend(
            bronze_bucket=os.environ.get("S3_BRONZE_BUCKET", ""),
            runs_bucket=os.environ.get("S3_RUNS_BUCKET") or None,
            prefix=os.environ.get("S3_PREFIX", ""),
            kms_key_id=os.environ.get("S3_KMS_KEY_ID") or None,
        )
    raise ValueError(f"STORAGE_BACKEND must be 'local' or 's3', got {kind!r}")


storage = Storage(backend_from_env())

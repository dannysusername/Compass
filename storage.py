"""Storage backend for uploaded files (syllabus PDFs and class documents).

Two modes, picked from `STORAGE_BACKEND` env var:
  - "local" (default, dev): files live under ./uploads/
  - "s3"                : S3-compatible bucket (Cloudflare R2, AWS S3, Backblaze)

Heroku's filesystem is ephemeral — every dyno restart wipes /app — so prod
must use object storage. The keys we store in the DB (`Syllabus.filename`,
`Document.filename`) are opaque to callers: the storage layer maps them to
either a local path or an S3 object key.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi.responses import FileResponse, Response, StreamingResponse


UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

BACKEND = os.environ.get("STORAGE_BACKEND", "local").strip().lower()

_s3 = None
_bucket: Optional[str] = None

if BACKEND == "s3":
    import boto3
    from botocore.config import Config

    _bucket = os.environ.get("STORAGE_BUCKET", "").strip()
    if not _bucket:
        raise RuntimeError("STORAGE_BACKEND=s3 requires STORAGE_BUCKET")
    _s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL") or None,
        aws_access_key_id=os.environ.get("STORAGE_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("STORAGE_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("STORAGE_REGION", "auto"),
        config=Config(signature_version="s3v4"),
    )


def save(filename: str, content: bytes, content_type: str = "application/octet-stream") -> None:
    if BACKEND == "s3":
        _s3.put_object(Bucket=_bucket, Key=filename, Body=content, ContentType=content_type)
    else:
        (UPLOAD_DIR / filename).write_bytes(content)


def read(filename: str) -> bytes:
    if BACKEND == "s3":
        obj = _s3.get_object(Bucket=_bucket, Key=filename)
        return obj["Body"].read()
    return (UPLOAD_DIR / filename).read_bytes()


def delete(filename: str) -> None:
    if BACKEND == "s3":
        try:
            _s3.delete_object(Bucket=_bucket, Key=filename)
        except Exception:
            pass  # missing-on-delete is fine
    else:
        try:
            (UPLOAD_DIR / filename).unlink(missing_ok=True)
        except Exception:
            pass


def exists(filename: str) -> bool:
    if BACKEND == "s3":
        try:
            _s3.head_object(Bucket=_bucket, Key=filename)
            return True
        except Exception:
            return False
    return (UPLOAD_DIR / filename).exists()


def serve(filename: str, content_type: Optional[str] = None) -> Response:
    """Return a FastAPI response that streams the file to the client.
    Used by the `/uploads/{filename}` route. For S3 we proxy the bytes
    through the app rather than redirecting — keeps the bucket private."""
    if BACKEND == "s3":
        obj = _s3.get_object(Bucket=_bucket, Key=filename)
        media_type = content_type or obj.get("ContentType") or "application/octet-stream"
        return StreamingResponse(obj["Body"].iter_chunks(), media_type=media_type)
    path = UPLOAD_DIR / filename
    return FileResponse(path, media_type=content_type) if content_type else FileResponse(path)

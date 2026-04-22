"""
Google Cloud Storage helpers — use *your* bucket as an alternative to the client's.

Configure via .env:
  GCS_ARTIFACTS_BUCKET       — required for chunk uploads (ingest scripts do not write local bodies)
  GCS_APPLICATION_CREDENTIALS — JSON **file** path **or** inline JSON (if value starts with ``{``)
  GOOGLE_APPLICATION_CREDENTIALS_JSON — optional **inline** service-account JSON (Railway-friendly; one line is safest)
  GOOGLE_APPLICATION_CREDENTIALS — file path **or** inline JSON (if value starts with ``{``)
  GCS_PROJECT_ID             — optional; Storage client project (default: key's project_id)
  GCS_ARTIFACTS_PREFIX       — optional object prefix, e.g. zmr-dev/prakash/

Chunk rows may store gs://your-bucket/... in chunk_gcs_uri; that bucket must grant
your SA roles/storage.objectAdmin (or objectViewer for read-only retrieval).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from google.cloud import storage


def _strip_inline_env_json(raw: str) -> str:
    """Railway/editor sometimes add a BOM or stray whitespace before ``{``."""
    s = (raw or "").strip()
    if s.startswith("\ufeff"):
        s = s.lstrip("\ufeff").strip()
    return s


def _load_service_account_info_from_env() -> Optional[Dict[str, Any]]:
    """Parse inline JSON from env (Railway often cannot mount a key file)."""
    for key in ("GOOGLE_APPLICATION_CREDENTIALS_JSON", "GCS_SERVICE_ACCOUNT_JSON"):
        raw = _strip_inline_env_json(os.getenv(key) or "")
        if raw.startswith("{"):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                continue
    raw_g = _strip_inline_env_json(os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "")
    if raw_g.startswith("{"):
        try:
            return json.loads(raw_g)
        except json.JSONDecodeError:
            pass
    raw_c = _strip_inline_env_json(os.getenv("GCS_APPLICATION_CREDENTIALS") or "")
    if raw_c.startswith("{"):
        try:
            return json.loads(raw_c)
        except json.JSONDecodeError:
            pass
    return None


def _env_json_shape(key: str) -> str:
    """Non-secret hint: whether an env value looks like inline JSON and if it parses."""
    raw = _strip_inline_env_json(os.getenv(key) or "")
    if not raw:
        return "unset"
    if not raw.startswith("{"):
        return "not_json_string"
    try:
        json.loads(raw)
        return "inline_json_parse_ok"
    except json.JSONDecodeError:
        return "inline_json_parse_failed"


def gcs_credentials_mode() -> Dict[str, Any]:
    """Non-secret summary for ``/v1/retrieval-status`` (how GCS reads will authenticate)."""
    info = _load_service_account_info_from_env()
    if info:
        return {
            "mode": "inline_json",
            "client_email": info.get("client_email"),
            "project_id": info.get("project_id"),
        }
    path = _credentials_json_path()
    if path:
        return {"mode": "json_file", "file_name": path.name}
    return {
        "mode": "adc_default",
        "note": "No JSON file or inline JSON found; google.cloud uses Application Default Credentials "
        "(often missing on Railway). Chunk bodies in gs:// will not load unless you set "
        "GOOGLE_APPLICATION_CREDENTIALS_JSON or a path to a key file.",
        "env_json_shape": {
            "GOOGLE_APPLICATION_CREDENTIALS": _env_json_shape("GOOGLE_APPLICATION_CREDENTIALS"),
            "GCS_APPLICATION_CREDENTIALS": _env_json_shape("GCS_APPLICATION_CREDENTIALS"),
            "GOOGLE_APPLICATION_CREDENTIALS_JSON": _env_json_shape(
                "GOOGLE_APPLICATION_CREDENTIALS_JSON"
            ),
        },
    }


def gcs_bucket_probe(*, timeout_sec: float = 12.0) -> Dict[str, Any]:
    """
    Lightweight GCS check aligned with **chunk reads** (``storage.objects.list``), not
    ``storage.buckets.get``. Object Viewer on the bucket can list objects but cannot
    call :meth:`google.cloud.storage.Bucket.exists`, which would falsely show failure.
    """
    name = (os.getenv("GCS_ARTIFACTS_BUCKET") or "").strip()
    if not name:
        return {"ok": False, "reason": "GCS_ARTIFACTS_BUCKET unset"}
    try:
        client = storage_client()
        bucket = client.bucket(name)
        # One list call — same permission family as reading chunk bodies.
        next(bucket.list_blobs(max_results=1, timeout=timeout_sec), None)
        return {"ok": True, "bucket": name, "object_list_ok": True}
    except Exception as e:
        return {"ok": False, "bucket": name, "error": str(e)[:400]}


def _credentials_json_path() -> Optional[Path]:
    p = os.getenv("GCS_APPLICATION_CREDENTIALS", "").strip()
    if p and Path(p).is_file():
        return Path(p).resolve()
    p2 = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if p2 and Path(p2).is_file():
        return Path(p2).resolve()
    root = Path(__file__).resolve().parent.parent
    default = root / "service-account-key.json"
    if default.is_file():
        return default.resolve()
    return None


def storage_client() -> storage.Client:
    """Client for GCS — inline JSON env, then key file path, then ADC."""
    project = (
        os.getenv("GCS_PROJECT_ID", "").strip()
        or os.getenv("GCLOUD_PROJECT_ID", "").strip()
        or None
    )
    info = _load_service_account_info_from_env()
    if info:
        pid = project or info.get("project_id")
        return storage.Client.from_service_account_info(info, project=pid)
    path = _credentials_json_path()
    if path:
        return storage.Client.from_service_account_json(str(path), project=project)
    return storage.Client(project=project)


def parse_gs_uri(uri: str) -> Tuple[str, str]:
    u = (uri or "").strip()
    if not u.startswith("gs://"):
        raise ValueError(f"Not a gs:// URI: {uri!r}")
    rest = u[5:]
    if "/" not in rest:
        raise ValueError(f"Invalid gs:// URI (missing object path): {uri!r}")
    bucket, _, blob = rest.partition("/")
    if not bucket or not blob:
        raise ValueError(f"Invalid gs:// URI: {uri!r}")
    return bucket, blob


def download_blob_text(gs_uri: str, *, encoding: str = "utf-8") -> str:
    """Read object body as text (for RAG context). Uses credentials from gcs_client rules."""
    bucket_name, blob_name = parse_gs_uri(gs_uri)
    client = storage_client()
    blob = client.bucket(bucket_name).blob(blob_name)
    return blob.download_as_text(encoding=encoding)


def artifacts_bucket_name() -> str:
    name = os.getenv("GCS_ARTIFACTS_BUCKET", "").strip()
    if not name:
        raise RuntimeError(
            "GCS_ARTIFACTS_BUCKET is not set. Ingestion stores chunk bodies only in GCS; "
            "set this in .env to your artifacts bucket."
        )
    return name


def require_gcs_artifacts_bucket_env() -> str:
    """Validate bucket for ingest CLIs and pin stripped ``GCS_ARTIFACTS_BUCKET`` in the environment."""
    name = artifacts_bucket_name()
    os.environ["GCS_ARTIFACTS_BUCKET"] = name
    return name


def _prefix() -> str:
    p = os.getenv("GCS_ARTIFACTS_PREFIX", "").strip().strip("/")
    return f"{p}/" if p else ""


def upload_text(
    text: str,
    *,
    content_type: str = "text/plain; charset=utf-8",
    object_name: Optional[str] = None,
) -> str:
    """
    Upload UTF-8 text to your artifacts bucket. Returns gs:// URI.

    object_name: optional full key under bucket; default GCS_ARTIFACTS_PREFIX + uuid.txt
    """
    bucket_name = artifacts_bucket_name()
    if object_name:
        key = object_name.lstrip("/")
    else:
        key = f"{_prefix()}{uuid.uuid4().hex}.txt"
    client = storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(key)
    body = text.decode("utf-8") if isinstance(text, bytes) else str(text)
    blob.upload_from_string(body, content_type=content_type)
    return f"gs://{bucket_name}/{key}"

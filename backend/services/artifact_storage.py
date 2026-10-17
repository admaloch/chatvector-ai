"""
Durable Ingestion Artifact Storage
==================================

Upload bytes are stored behind ``ArtifactStorage`` so Redis workers can fetch
payloads without sharing the API host's local disk.

Backends
--------
``local`` (default)
    Writes to ``QUEUE_SPILL_DIR``. Single-host Redis topologies keep the same
    spill files as before.

``s3``
    S3-compatible object storage (AWS S3, MinIO). Credentials come from env;
    clients never upload directly.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from core.config import config

logger = logging.getLogger(__name__)

TTL_METADATA_KEY = "artifact-ttl-seconds"


class ArtifactMissingError(Exception):
    """Raised when an artifact URI cannot be resolved to bytes."""

    def __init__(self, uri: str):
        self.uri = uri
        super().__init__(f"Artifact missing: {uri}")


class ArtifactUnreadableError(Exception):
    """Raised when an artifact exists but cannot be read."""

    def __init__(self, uri: str, reason: str):
        self.uri = uri
        self.reason = reason
        super().__init__(f"Artifact unreadable: {uri}")


class ArtifactStorage(ABC):
    """Store, fetch, and delete ingestion upload payloads."""

    @abstractmethod
    def put(self, doc_id: str, data: bytes) -> str:
        """Persist *data* and return an artifact URI the worker can fetch."""

    @abstractmethod
    def get(self, uri: str) -> bytes:
        """Return the bytes for *uri*.

        Raises ``ArtifactMissingError`` when the object is gone and
        ``ArtifactUnreadableError`` when it cannot be read.
        """

    @abstractmethod
    def delete(self, uri: str) -> None:
        """Delete *uri*. Missing objects are a no-op."""

    def ttl_marked(self, uri: str) -> bool:
        """True when the stored object carries a TTL / expiry mark."""
        return False


class LocalArtifactStorage(ArtifactStorage):
    """Filesystem backend used for single-host / local development."""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root or config.QUEUE_SPILL_DIR)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, doc_id: str, data: bytes) -> str:
        path = self._root / doc_id
        path.write_bytes(data)
        return path.resolve().as_uri()

    def get(self, uri: str) -> bytes:
        path = _local_path_from_uri(uri)
        if not path.exists():
            raise ArtifactMissingError(uri)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadableError(uri, str(exc)) from exc

    def delete(self, uri: str) -> None:
        path = _local_path_from_uri(uri)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove local artifact %s", path)


class S3ArtifactStorage(ArtifactStorage):
    """S3-compatible backend. Lazy-imports boto3 so local mode stays light."""

    def __init__(
        self,
        *,
        bucket: str | None = None,
        prefix: str | None = None,
        ttl_seconds: int | None = None,
        client=None,
    ) -> None:
        self.bucket = (bucket or config.ARTIFACT_S3_BUCKET or "").strip()
        if not self.bucket:
            raise ValueError("S3 artifact storage requires ARTIFACT_S3_BUCKET.")
        self.prefix = _normalize_prefix(
            prefix if prefix is not None else config.ARTIFACT_S3_PREFIX
        )
        self.ttl_seconds = (
            config.ARTIFACT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        )
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _build_s3_client()
        return self._client

    def put(self, doc_id: str, data: bytes) -> str:
        key = f"{self.prefix}{doc_id}"
        extra: dict = {}
        if self.ttl_seconds > 0:
            extra["Metadata"] = {TTL_METADATA_KEY: str(self.ttl_seconds)}
            extra["Expires"] = datetime.now(timezone.utc) + timedelta(
                seconds=self.ttl_seconds
            )
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        return f"s3://{self.bucket}/{key}"

    def get(self, uri: str) -> bytes:
        bucket, key = _parse_s3_uri(uri)
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if _is_s3_not_found(exc):
                raise ArtifactMissingError(uri) from exc
            raise ArtifactUnreadableError(uri, str(exc)) from exc
        try:
            return response["Body"].read()
        except Exception as exc:
            raise ArtifactUnreadableError(uri, str(exc)) from exc

    def delete(self, uri: str) -> None:
        bucket, key = _parse_s3_uri(uri)
        try:
            self.client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if _is_s3_not_found(exc):
                return
            logger.warning("Could not delete S3 artifact %s: %s", uri, exc)

    def ttl_marked(self, uri: str) -> bool:
        bucket, key = _parse_s3_uri(uri)
        try:
            response = self.client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if _is_s3_not_found(exc):
                return False
            raise
        metadata = response.get("Metadata") or {}
        if metadata.get(TTL_METADATA_KEY):
            return True
        return response.get("Expires") is not None


_storage: ArtifactStorage | None = None
_storage_lock = threading.Lock()


def get_artifact_storage() -> ArtifactStorage:
    """Return the process-wide artifact backend (lazy, config-selected)."""
    global _storage
    if _storage is not None:
        return _storage
    with _storage_lock:
        if _storage is None:
            _storage = _create_artifact_storage()
        return _storage


def reset_artifact_storage() -> None:
    """Clear the cached backend — for tests only."""
    global _storage
    with _storage_lock:
        _storage = None


def _create_artifact_storage() -> ArtifactStorage:
    backend = config.ARTIFACT_STORAGE
    if backend == "s3":
        return S3ArtifactStorage()
    return LocalArtifactStorage()


def _normalize_prefix(prefix: str) -> str:
    cleaned = (prefix or "").strip().lstrip("/")
    if cleaned and not cleaned.endswith("/"):
        cleaned += "/"
    return cleaned


def _local_path_from_uri(uri: str) -> Path:
    if uri.startswith("file:"):
        parsed = urlparse(uri)
        path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc != "localhost":
            path = f"/{parsed.netloc}{path}"
        return Path(path)
    return Path(uri)


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise ArtifactMissingError(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def _build_s3_client():
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:
        raise RuntimeError(
            "ARTIFACT_STORAGE=s3 requires the boto3 package. "
            "Install backend requirements or switch ARTIFACT_STORAGE=local."
        ) from exc

    kwargs: dict = {"region_name": config.ARTIFACT_S3_REGION}
    if config.ARTIFACT_S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = config.ARTIFACT_S3_ENDPOINT_URL
    if config.ARTIFACT_S3_ACCESS_KEY and config.ARTIFACT_S3_SECRET_KEY:
        kwargs["aws_access_key_id"] = config.ARTIFACT_S3_ACCESS_KEY
        kwargs["aws_secret_access_key"] = config.ARTIFACT_S3_SECRET_KEY
    if config.ARTIFACT_S3_FORCE_PATH_STYLE:
        kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
    return boto3.client("s3", **kwargs)


def _is_s3_not_found(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") or {}
        code = str(error.get("Code") or "")
        if code in {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}:
            return True
    name = type(exc).__name__
    return name in {"NoSuchKey", "404", "NotFound"}

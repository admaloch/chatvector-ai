"""Unit tests for local and S3-compatible artifact storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import Settings, VALID_ARTIFACT_STORAGES, _validate_artifact_storage
from services.artifact_storage import (
    ArtifactMissingError,
    LocalArtifactStorage,
    S3ArtifactStorage,
    get_artifact_storage,
    reset_artifact_storage,
)


def test_validate_artifact_storage_rejects_unknown_backend():
    settings = Settings()
    settings.ARTIFACT_STORAGE = "gcs"
    with pytest.raises(ValueError, match="Invalid ARTIFACT_STORAGE"):
        _validate_artifact_storage(settings)


def test_validate_artifact_storage_requires_bucket_for_s3():
    settings = Settings()
    settings.ARTIFACT_STORAGE = "s3"
    settings.ARTIFACT_S3_BUCKET = None
    with pytest.raises(ValueError, match="ARTIFACT_S3_BUCKET"):
        _validate_artifact_storage(settings)


def test_valid_artifact_storages():
    assert VALID_ARTIFACT_STORAGES == {"local", "s3"}


def test_local_put_get_delete_roundtrip(tmp_path):
    storage = LocalArtifactStorage(tmp_path)
    uri = storage.put("doc-local", b"hello-bytes")

    assert uri.startswith("file:")
    assert (tmp_path / "doc-local").read_bytes() == b"hello-bytes"
    assert storage.get(uri) == b"hello-bytes"

    storage.delete(uri)
    assert not (tmp_path / "doc-local").exists()
    with pytest.raises(ArtifactMissingError):
        storage.get(uri)


def test_local_get_accepts_raw_filesystem_path(tmp_path):
    path = tmp_path / "doc-raw"
    path.write_bytes(b"raw-path")
    storage = LocalArtifactStorage(tmp_path)
    assert storage.get(str(path)) == b"raw-path"


def test_local_delete_missing_is_noop(tmp_path):
    storage = LocalArtifactStorage(tmp_path)
    storage.delete(str(tmp_path / "never-existed"))


def test_factory_defaults_to_local(monkeypatch, tmp_path):
    monkeypatch.setattr("services.artifact_storage.config.ARTIFACT_STORAGE", "local")
    monkeypatch.setattr("services.artifact_storage.config.QUEUE_SPILL_DIR", str(tmp_path))
    reset_artifact_storage()
    try:
        storage = get_artifact_storage()
        assert isinstance(storage, LocalArtifactStorage)
        uri = storage.put("doc-factory", b"x")
        assert storage.get(uri) == b"x"
    finally:
        reset_artifact_storage()


def _s3_storage(bucket: str = "chatvector-artifacts") -> S3ArtifactStorage:
    boto3 = pytest.importorskip("boto3")
    pytest.importorskip("moto")
    from moto import mock_aws

    mock = mock_aws()
    mock.start()
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=bucket)
    storage = S3ArtifactStorage(
        bucket=bucket,
        prefix="chatvector/artifacts/",
        ttl_seconds=3600,
        client=client,
    )
    storage._moto = mock  # type: ignore[attr-defined]
    return storage


@pytest.fixture
def s3_storage():
    storage = _s3_storage()
    try:
        yield storage
    finally:
        storage._moto.stop()  # type: ignore[attr-defined]


def test_s3_put_get_delete_roundtrip(s3_storage):
    uri = s3_storage.put("doc-s3", b"s3-bytes")
    assert uri == "s3://chatvector-artifacts/chatvector/artifacts/doc-s3"
    assert s3_storage.get(uri) == b"s3-bytes"

    s3_storage.delete(uri)
    with pytest.raises(ArtifactMissingError):
        s3_storage.get(uri)


def test_s3_put_marks_ttl(s3_storage):
    uri = s3_storage.put("doc-ttl", b"ttl-bytes")
    assert s3_storage.ttl_marked(uri) is True


def test_s3_missing_artifact_raises(s3_storage):
    with pytest.raises(ArtifactMissingError):
        s3_storage.get("s3://chatvector-artifacts/chatvector/artifacts/missing")


def test_s3_delete_missing_is_noop(s3_storage):
    s3_storage.delete("s3://chatvector-artifacts/chatvector/artifacts/missing")


def test_s3_without_ttl_is_not_marked():
    boto3 = pytest.importorskip("boto3")
    pytest.importorskip("moto")
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="no-ttl")
        storage = S3ArtifactStorage(
            bucket="no-ttl",
            prefix="p/",
            ttl_seconds=0,
            client=client,
        )
        uri = storage.put("doc-no-ttl", b"x")
        assert storage.ttl_marked(uri) is False

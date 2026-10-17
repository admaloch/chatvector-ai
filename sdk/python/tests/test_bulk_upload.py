"""Tests for client-side bulk document upload helpers."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from chatvector import (
    BulkUploadSummary,
    ChatVectorAPIError,
    ChatVectorClient,
    ChatVectorRateLimitError,
    DocumentResponse,
)
from chatvector._bulk_upload import collect_directory_upload_paths


class BulkUploadTests(unittest.TestCase):
    """Exercise upload_many and upload_directory behavior."""

    def setUp(self) -> None:
        self.client = ChatVectorClient("https://api.chatvector.test", api_key="token")
        self.addCleanup(self.client.close)

    def test_upload_many_respects_concurrency_cap(self) -> None:
        in_flight = 0
        max_in_flight = 0
        lock = threading.Lock()
        gate = threading.Event()
        gate.set()

        def fake_upload(file_path: str, **kwargs: object) -> DocumentResponse:
            nonlocal in_flight, max_in_flight
            with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            gate.wait(timeout=2)
            with lock:
                in_flight -= 1
            return DocumentResponse.from_dict(
                {"document_id": Path(file_path).name, "status": "queued"},
            )

        with patch.object(self.client, "upload_document", side_effect=fake_upload):
            with tempfile.TemporaryDirectory() as tmp:
                paths = []
                for index in range(8):
                    path = Path(tmp) / f"doc-{index}.txt"
                    path.write_text("x", encoding="utf-8")
                    paths.append(str(path))
                summary = self.client.upload_many(paths, concurrency=3)

        self.assertLessEqual(max_in_flight, 3)
        self.assertEqual(summary.count, 8)
        self.assertEqual(summary.success_count, 8)

    def test_partial_failure_continues_and_reports_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ok_path = Path(tmp) / "ok.txt"
            bad_path = Path(tmp) / "bad.txt"
            ok_path.write_text("ok", encoding="utf-8")
            bad_path.write_text("bad", encoding="utf-8")

            def fake_upload(file_path: str, **kwargs: object) -> DocumentResponse:
                if Path(file_path).name == "bad.txt":
                    raise ChatVectorAPIError("boom", status_code=500)
                return DocumentResponse.from_dict(
                    {"document_id": "doc-ok", "status": "queued"},
                )

            with patch.object(self.client, "upload_document", side_effect=fake_upload):
                summary = self.client.upload_many([str(ok_path), str(bad_path)])

        self.assertIsInstance(summary, BulkUploadSummary)
        self.assertEqual(summary.success_count, 1)
        self.assertEqual(summary.failure_count, 1)
        self.assertFalse(summary.results[1].success)
        self.assertIsInstance(summary.results[1].error, ChatVectorAPIError)

    def test_rate_limit_surfaces_per_file_without_retry_storm(self) -> None:
        attempts = 0

        def fake_upload(file_path: str, **kwargs: object) -> DocumentResponse:
            nonlocal attempts
            attempts += 1
            raise ChatVectorRateLimitError("limited", status_code=429)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            handle.write(b"x")
            path = handle.name

        self.addCleanup(lambda: os.unlink(path))
        with patch.object(self.client, "upload_document", side_effect=fake_upload):
            summary = self.client.upload_many([path, path])

        self.assertEqual(attempts, 2)
        self.assertEqual(summary.failure_count, 2)
        self.assertIsInstance(summary.results[0].error, ChatVectorRateLimitError)

    def test_directory_helper_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            outside = Path(tmp) / "outside.txt"
            root.mkdir()
            outside.write_text("secret", encoding="utf-8")
            link = root / "escape.txt"
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                collect_directory_upload_paths(str(root))

    def test_directory_helper_rejects_dot_dot_segments(self) -> None:
        with self.assertRaises(ValueError):
            collect_directory_upload_paths("/tmp/../etc")


if __name__ == "__main__":
    unittest.main()

"""Supported-format MIME mapping that does not depend on the host mimetypes DB."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from chatvector import AsyncChatVectorClient, ChatVectorClient
from chatvector._common import content_type_for_filename
from test_client import make_response

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SUPPORTED = {
    "guide.pdf": "application/pdf",
    "notes.txt": "text/plain",
    "readme.md": "text/markdown",
    "page.html": "text/html",
    "brief.docx": DOCX_MIME,
}


def _upload_ok():
    return make_response(
        202,
        method="POST",
        url="https://api.chatvector.test/ingest",
        json_data={"message": "Accepted", "document_id": "doc-1", "status": "queued"},
    )


class ContentTypeMappingTests(unittest.TestCase):
    def test_supported_extensions_ignore_host_guess(self) -> None:
        with patch(
            "chatvector._common.mimetypes.guess_type",
            return_value=(None, None),
        ):
            for filename, expected in SUPPORTED.items():
                self.assertEqual(content_type_for_filename(filename), expected)

        with patch(
            "chatvector._common.mimetypes.guess_type",
            return_value=("application/octet-stream", None),
        ):
            for filename, expected in SUPPORTED.items():
                self.assertEqual(content_type_for_filename(filename), expected)

    def test_unsupported_names_still_fall_back(self) -> None:
        with patch(
            "chatvector._common.mimetypes.guess_type",
            return_value=(None, None),
        ):
            self.assertEqual(
                content_type_for_filename("image.png"),
                "application/octet-stream",
            )
        with patch(
            "chatvector._common.mimetypes.guess_type",
            return_value=("image/png", None),
        ):
            self.assertEqual(content_type_for_filename("image.png"), "image/png")


class SyncUploadMimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ChatVectorClient("https://api.chatvector.test", api_key="token")
        self.addCleanup(self.client.close)

    def test_upload_sends_mapped_types_when_guess_type_is_empty(self) -> None:
        directory = Path(__file__).resolve().parent
        created: list[Path] = []
        try:
            with patch(
                "chatvector._common.mimetypes.guess_type",
                return_value=(None, None),
            ):
                for filename, expected in SUPPORTED.items():
                    path = directory / f"mime-{filename}"
                    path.write_bytes(b"content")
                    created.append(path)
                    with patch.object(
                        self.client._client, "request", return_value=_upload_ok()
                    ) as mock_request:
                        self.client.upload_document(str(path))
                    self.assertEqual(
                        mock_request.call_args.kwargs["files"]["file"][2],
                        expected,
                    )
        finally:
            for path in created:
                path.unlink(missing_ok=True)


class AsyncUploadMimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = AsyncChatVectorClient(
            "https://api.chatvector.test", api_key="token"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_upload_sends_mapped_types_when_guess_type_is_empty(self) -> None:
        directory = Path(__file__).resolve().parent
        created: list[Path] = []
        try:
            with patch(
                "chatvector._common.mimetypes.guess_type",
                return_value=(None, None),
            ):
                for filename, expected in SUPPORTED.items():
                    path = directory / f"async-mime-{filename}"
                    path.write_bytes(b"content")
                    created.append(path)
                    with patch.object(
                        self.client._client,
                        "request",
                        new=AsyncMock(return_value=_upload_ok()),
                    ) as mock_request:
                        await self.client.upload_document(str(path))
                    self.assertEqual(
                        mock_request.call_args.kwargs["files"]["file"][2],
                        expected,
                    )
        finally:
            for path in created:
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

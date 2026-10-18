"""Upload metadata multipart serialization for sync and async clients."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from chatvector import AsyncChatVectorClient, ChatVectorClient
from test_client import make_response


def _upload_ok():
    return make_response(
        202,
        method="POST",
        url="https://api.chatvector.test/ingest",
        json_data={
            "message": "Accepted",
            "document_id": "doc-123",
            "status": "queued",
        },
    )


class UploadMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ChatVectorClient("https://api.chatvector.test", api_key="token")
        self.addCleanup(self.client.close)
        self.file_path = Path(__file__).resolve().parent / "metadata-upload.txt"
        self.file_path.write_text("hello", encoding="utf-8")
        self.addCleanup(self.file_path.unlink, missing_ok=True)

    def test_metadata_is_sent_as_json_and_omitted_when_unset(self) -> None:
        metadata = {"department": "legal", "active": True}
        with patch.object(
            self.client._client, "request", return_value=_upload_ok()
        ) as mock_request:
            self.client.upload_document(str(self.file_path), metadata=metadata)

        sent = mock_request.call_args.kwargs["data"]["metadata"]
        self.assertEqual(json.loads(sent), metadata)

        with patch.object(
            self.client._client, "request", return_value=_upload_ok()
        ) as mock_request:
            self.client.upload_document(str(self.file_path))

        self.assertNotIn("data", mock_request.call_args.kwargs)


class AsyncUploadMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = AsyncChatVectorClient(
            "https://api.chatvector.test", api_key="token"
        )
        self.file_path = Path(__file__).resolve().parent / "metadata-upload-async.txt"
        self.file_path.write_text("hello", encoding="utf-8")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.file_path.unlink(missing_ok=True)

    async def test_metadata_is_sent_as_json_and_omitted_when_unset(self) -> None:
        metadata = {"department": "legal", "active": True}
        with patch.object(
            self.client._client,
            "request",
            new=AsyncMock(return_value=_upload_ok()),
        ) as mock_request:
            await self.client.upload_document(str(self.file_path), metadata=metadata)

        sent = mock_request.call_args.kwargs["data"]["metadata"]
        self.assertEqual(json.loads(sent), metadata)

        with patch.object(
            self.client._client,
            "request",
            new=AsyncMock(return_value=_upload_ok()),
        ) as mock_request:
            await self.client.upload_document(str(self.file_path))

        self.assertNotIn("data", mock_request.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()

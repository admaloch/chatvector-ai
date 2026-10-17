"""SDK surface parity tests for issue #496."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from chatvector import AsyncChatVectorClient, ChatVectorClient
from chatvector._sse import async_iter_document_status_events, map_document_status_stream_error
from chatvector.models import ChatSource, RetrievalDebugMetadata


def status_sse_lines() -> list[str]:
    return [
        "event: status",
        'data: {"document_id":"doc-123","status":"queued"}',
        "",
        "event: status",
        'data: {"document_id":"doc-123","status":"completed","chunks":{"total":1,"processed":1}}',
        "",
    ]


class MockAsyncStreamResponse:
    def __init__(self, *, lines: list[str]) -> None:
        self.status_code = 200
        self._lines = list(lines)
        self.request = httpx.Request(
            "GET",
            "https://api.chatvector.test/documents/doc-123/status/stream",
        )

    @property
    def is_success(self) -> bool:
        return True

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aclose(self) -> None:
        return None


class MockAsyncStreamContext:
    def __init__(self, response: MockAsyncStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> MockAsyncStreamResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._response.aclose()


class TestSdkSurfaceParity(unittest.TestCase):
    def test_chat_source_parses_component_scores(self) -> None:
        source = ChatSource.from_dict(
            {
                "file_name": "guide.pdf",
                "page_number": 1,
                "chunk_index": 0,
                "score": 0.9,
                "score_type": "hybrid_rrf",
                "vector_score": 0.8,
                "full_text_score": 0.7,
                "rrf_score": 0.65,
                "reranker_score": 0.95,
                "rerank_order": 1,
            }
        )
        self.assertEqual(source.vector_score, 0.8)
        self.assertEqual(source.rerank_order, 1)
        payload = source.to_dict()
        self.assertEqual(payload["vector_score"], 0.8)
        self.assertEqual(payload["rerank_order"], 1)

    def test_retrieval_debug_preserves_unknown_fields(self) -> None:
        debug = RetrievalDebugMetadata.from_dict(
            {
                "original_query": "What is RAG?",
                "transformed_queries": ["Define RAG"],
                "future_trace_field": {"step": 1},
            }
        )
        self.assertEqual(debug.original_query, "What is RAG?")
        self.assertEqual(debug.passthrough["future_trace_field"], {"step": 1})
        round_trip = debug.to_dict()
        self.assertEqual(round_trip["future_trace_field"], {"step": 1})
        self.assertEqual(round_trip["original_query"], "What is RAG?")

    def test_upload_document_sends_session_header(self) -> None:
        client = ChatVectorClient("https://api.chatvector.test")
        captured: dict[str, object] = {}

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            response = httpx.Response(
                202,
                json={
                    "document_id": "doc-123",
                    "status": "queued",
                },
                request=httpx.Request(method, url),
            )
            return response

        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "guide.pdf"
            file_path.write_bytes(b"%PDF-1.4")
            with patch.object(client._client, "request", side_effect=fake_request):
                client.upload_document(str(file_path), session_id="sess-99")

        self.assertEqual(captured["headers"], {"X-Session-Id": "sess-99"})

    def test_chat_debug_retrieval_request_and_response(self) -> None:
        client = ChatVectorClient("https://api.chatvector.test")
        response = httpx.Response(
            200,
            json={
                "question": "Q",
                "doc_id": "doc-123",
                "chunks": 1,
                "answer": "A",
                "sources": [],
                "retrieval_debug": {
                    "original_query": "Q",
                    "future_field": True,
                },
            },
            request=httpx.Request("POST", "https://api.chatvector.test/chat"),
        )

        with patch.object(client._client, "request", return_value=response) as mock_request:
            result = client.chat("Q", "doc-123", debug_retrieval=True)

        body = mock_request.call_args.kwargs["json"]
        self.assertTrue(body["debug_retrieval"])
        self.assertIsNotNone(result.retrieval_debug)
        assert result.retrieval_debug is not None
        self.assertEqual(result.retrieval_debug.original_query, "Q")
        self.assertEqual(result.retrieval_debug.passthrough["future_field"], True)

    def test_async_iter_document_status_events_parses_terminal_status(self) -> None:
        async def collect() -> list[str]:
            statuses = []
            async for status in async_iter_document_status_events(
                _async_lines(status_sse_lines()),
                map_error=map_document_status_stream_error,
            ):
                statuses.append(status.status)
            return statuses

        statuses = asyncio.run(collect())
        self.assertEqual(statuses, ["queued", "completed"])

    def test_async_client_iter_document_status_uses_stream_endpoint(self) -> None:
        client = AsyncChatVectorClient("https://api.chatvector.test")
        mock_response = MockAsyncStreamResponse(lines=status_sse_lines())

        with patch.object(
            client._client,
            "stream",
            return_value=MockAsyncStreamContext(mock_response),
        ) as mock_stream:
            statuses = asyncio.run(_collect_async_statuses(client))

        mock_stream.assert_called_once()
        self.assertIn("status/stream", mock_stream.call_args.args[1])
        self.assertEqual(statuses, ["queued", "completed"])


async def _collect_async_statuses(client: AsyncChatVectorClient) -> list[str]:
    collected: list[str] = []
    async for status in client.iter_document_status("doc-123"):
        collected.append(status.status)
    return collected


async def _async_lines(lines: list[str]):
    for line in lines:
        yield line


if __name__ == "__main__":
    unittest.main()

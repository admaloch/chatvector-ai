"""SDK unit tests for feedback endpoints."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import httpx

from chatvector import ChatVectorClient
from chatvector.models import FeedbackItem


class TestFeedbackSdk(unittest.TestCase):
    def test_post_feedback_maps_response(self) -> None:
        client = ChatVectorClient("https://api.chatvector.test", api_key="key")
        response = httpx.Response(
            201,
            json={
                "id": "fb-1",
                "type": "answer",
                "rating": 1,
                "session_id": "sess-1",
                "message_id": "msg-1",
                "created_at": "2026-01-01T00:00:00Z",
            },
            request=httpx.Request("POST", "https://api.chatvector.test/feedback"),
        )
        with patch.object(client._client, "request", return_value=response) as mock_request:
            item = client.post_feedback(
                feedback_type="answer",
                rating=1,
                session_id="sess-1",
                message_id="msg-1",
            )
        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args.kwargs
        self.assertEqual(call_kwargs["json"]["type"], "answer")
        self.assertEqual(call_kwargs["json"]["session_id"], "sess-1")
        self.assertIsInstance(item, FeedbackItem)
        self.assertEqual(item.message_id, "msg-1")

    def test_list_feedback_builds_query_params(self) -> None:
        client = ChatVectorClient("https://api.chatvector.test", api_key="key")
        response = httpx.Response(
            200,
            json={"feedback": []},
            request=httpx.Request("GET", "https://api.chatvector.test/feedback"),
        )
        with patch.object(client._client, "request", return_value=response) as mock_request:
            listed = client.list_feedback(session_id="sess-1", limit=25)
        self.assertEqual(listed.feedback, [])
        params = mock_request.call_args.kwargs.get("params")
        self.assertEqual(params["session_id"], "sess-1")
        self.assertEqual(params["limit"], 25)


if __name__ == "__main__":
    unittest.main()

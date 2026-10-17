"""Tests for RetrievalConfig and HistoryConfig request serialization."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from chatvector import (
    ChatVectorClient,
    HistoryConfig,
    RetrievalConfig,
)
from chatvector.models import validate_match_count
from test_client import make_response


class RequestConfigTests(unittest.TestCase):
    """Exercise nested retrieval/history config on chat requests."""

    def setUp(self) -> None:
        self.client = ChatVectorClient("https://api.chatvector.test", api_key="token")
        self.addCleanup(self.client.close)

    def test_retrieval_and_history_serialize_to_nested_objects(self) -> None:
        response = make_response(
            200,
            method="POST",
            url="https://api.chatvector.test/chat",
            json_data={"question": "Q", "chunks": 0, "answer": "A", "sources": []},
        )
        retrieval = RetrievalConfig(
            match_count=8,
            hybrid_enabled=True,
            query_transformation_strategy="expand",
        )
        history = HistoryConfig(
            retrieval_history_window=4,
            history_aware_retrieval=False,
        )

        with patch.object(self.client._client, "request", return_value=response) as mock_request:
            self.client.chat("Q", "doc-1", retrieval=retrieval, history=history)

        body = mock_request.call_args.kwargs["json"]
        self.assertEqual(
            body["retrieval"],
            {
                "match_count": 8,
                "hybrid_enabled": True,
                "query_transformation_strategy": "expand",
            },
        )
        self.assertEqual(
            body["history"],
            {
                "retrieval_history_window": 4,
                "history_aware_retrieval": False,
            },
        )

    def test_empty_config_objects_are_omitted(self) -> None:
        response = make_response(
            200,
            method="POST",
            url="https://api.chatvector.test/chat",
            json_data={"question": "Q", "chunks": 0, "answer": "A", "sources": []},
        )

        with patch.object(self.client._client, "request", return_value=response) as mock_request:
            self.client.chat(
                "Q",
                "doc-1",
                retrieval=RetrievalConfig(),
                history=HistoryConfig(),
            )

        body = mock_request.call_args.kwargs["json"]
        self.assertNotIn("retrieval", body)
        self.assertNotIn("history", body)

    def test_unset_fields_are_not_sent_as_null(self) -> None:
        payload = RetrievalConfig(hybrid_enabled=True).to_dict()
        self.assertEqual(payload, {"hybrid_enabled": True})
        self.assertNotIn("match_count", payload)

    def test_same_config_instance_is_not_mutated_across_calls(self) -> None:
        response = make_response(
            200,
            method="POST",
            url="https://api.chatvector.test/chat",
            json_data={"question": "Q", "chunks": 0, "answer": "A", "sources": []},
        )
        retrieval = RetrievalConfig(hybrid_enabled=True)

        with patch.object(self.client._client, "request", return_value=response):
            self.client.chat("Q", "doc-1", retrieval=retrieval)
            self.client.chat("Q", "doc-1", retrieval=retrieval)

        self.assertEqual(retrieval.to_dict(), {"hybrid_enabled": True})

    def test_match_count_zero_rejected_before_network(self) -> None:
        with self.assertRaises(ValueError):
            validate_match_count(0)
        with patch.object(self.client._client, "request") as mock_request:
            with self.assertRaises(ValueError):
                self.client.chat("Q", "doc-1", match_count=0)
        mock_request.assert_not_called()

    def test_retrieval_config_match_count_validated_on_construction(self) -> None:
        with self.assertRaises(ValueError):
            RetrievalConfig(match_count=0)

    def test_chat_without_config_matches_phase3_body(self) -> None:
        response = make_response(
            200,
            method="POST",
            url="https://api.chatvector.test/chat",
            json_data={"question": "Hello?", "chunks": 1, "answer": "Hi.", "sources": []},
        )

        with patch.object(self.client._client, "request", return_value=response) as mock_request:
            self.client.chat("Hello?", "doc-123")

        self.assertEqual(
            mock_request.call_args.kwargs["json"],
            {"question": "Hello?", "doc_id": "doc-123", "match_count": 5},
        )


if __name__ == "__main__":
    unittest.main()

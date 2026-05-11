from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    mock_search_chain = MagicMock()
    mock_search_chain.invoke.return_value = {
        "answer": "Test answer.",
        "sources": ["notes/test.md"],
        "raw_context": "some context",
    }
    mock_store = MagicMock()
    mock_store.all_metadata.return_value = [{"source_file": "notes/test.md"}]

    import mdcore.serve.server as srv_module
    with (
        patch.object(srv_module, "_search_chain", mock_search_chain),
        patch.object(srv_module, "_ingest_chain", MagicMock()),
        patch("mdcore.serve.server.VectorStore", return_value=mock_store),
    ):
        yield TestClient(srv_module.app)


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_shape(self, client):
        data = client.get("/health").json()
        assert "status" in data
        assert "vault_path" in data
        assert "indexed_chunks" in data


class TestAskEndpoint:
    def test_ask_returns_200(self, client):
        resp = client.post("/ask", json={"query": "what is mTLS?"})
        assert resp.status_code == 200

    def test_ask_response_has_answer(self, client):
        data = client.post("/ask", json={"query": "test"}).json()
        assert "answer" in data
        assert "sources" in data
        assert "raw_context" in data

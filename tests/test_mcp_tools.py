from __future__ import annotations
import asyncio
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def srv():
    """Return mcp_server.server module with chains and store patched post-import."""
    mock_search_chain = MagicMock()
    mock_search_chain.invoke.return_value = {
        "answer": "Found relevant info.",
        "sources": ["notes/arch.md"],
        "raw_context": "ctx",
    }
    mock_ingest_chain = MagicMock()
    mock_ingest_chain.invoke.return_value = {
        "action": "new",
        "target_file": None,
        "suggested_folder": "projects",
        "proposal": "Create new file at projects/note.md",
        "confidence": 0.9,
    }
    mock_store = MagicMock()
    mock_store.all_metadata.return_value = [{"file_type": "md"}, {"file_type": "pdf"}]

    import mdcore.mcp_server.server as module
    with (
        patch.object(module, "_search_chain", mock_search_chain),
        patch.object(module, "_ingest_chain", mock_ingest_chain),
        patch("mdcore.mcp_server.server.VectorStore", return_value=mock_store),
    ):
        yield module


class TestHandleSearch:
    def test_returns_text_content(self, srv):
        result = asyncio.run(srv._handle_search({"query": "mTLS topology"}))
        assert len(result) == 1
        assert result[0].type == "text"
        assert "Found relevant info." in result[0].text

    def test_includes_sources(self, srv):
        result = asyncio.run(srv._handle_search({"query": "test"}))
        assert "notes/arch.md" in result[0].text

    def test_empty_query_returns_error(self, srv):
        result = asyncio.run(srv._handle_search({}))
        assert "Error" in result[0].text


class TestHandleIngest:
    def test_returns_proposal(self, srv):
        result = asyncio.run(srv._handle_ingest({"content": "Some note content here.", "title": "Test"}))
        assert len(result) == 1
        assert "Ingestion Proposal" in result[0].text

    def test_empty_content_returns_error(self, srv):
        result = asyncio.run(srv._handle_ingest({"content": ""}))
        assert "Error" in result[0].text


class TestHandleStatus:
    def test_returns_vault_info(self, srv):
        result = asyncio.run(srv._handle_status())
        assert "mdcore Vault Status" in result[0].text
        assert "Indexed chunks: 2" in result[0].text

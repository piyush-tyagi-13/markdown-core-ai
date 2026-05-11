from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest

from mdcore.config.models import (
    MdCoreConfig, VaultConfig, IndexerConfig, EmbeddingsConfig,
    VectorStoreConfig, RetrieverConfig, IngesterConfig, WriterConfig,
    LLMConfig, ManifestConfig, CLIConfig, LoggingConfig,
)


@pytest.fixture
def minimal_cfg(tmp_path):
    return MdCoreConfig(
        vault=VaultConfig(path=str(tmp_path)),
        indexer=IndexerConfig(),
        embeddings=EmbeddingsConfig(),
        vector_store=VectorStoreConfig(persist_path=str(tmp_path / "chroma")),
        retriever=RetrieverConfig(),
        ingester=IngesterConfig(),
        writer=WriterConfig(),
        llm=LLMConfig(),
        manifest=ManifestConfig(path=str(tmp_path / "manifest.json")),
        cli=CLIConfig(),
        logging=LoggingConfig(enabled=False),
    )


class TestBuildSearchChain:
    def test_chain_returns_required_keys_on_empty_index(self, minimal_cfg):
        mock_store = MagicMock()
        mock_store.all_metadata.return_value = []
        mock_engine = MagicMock()
        mock_llm = MagicMock()
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []

        with (
            patch("mdcore.serve.chain.VectorStore", return_value=mock_store),
            patch("mdcore.serve.chain.EmbeddingEngine", return_value=mock_engine),
            patch("mdcore.serve.chain.LLMLayer", return_value=mock_llm),
            patch("mdcore.serve.chain.KeywordPreFilter") as mock_pf_cls,
            patch("mdcore.serve.chain.VectorSearcher", return_value=mock_searcher),
        ):
            mock_pf_cls.return_value.filter.return_value = None
            from mdcore.serve.chain import build_search_chain
            chain = build_search_chain(minimal_cfg)
            result = chain.invoke({"query": "test query"})

        assert "answer" in result
        assert "sources" in result
        assert "raw_context" in result
        assert result["sources"] == []

    def test_chain_invokes_llm_when_chunks_found(self, minimal_cfg):
        from langchain_core.documents import Document

        mock_chunk = Document(
            page_content="relevant text",
            metadata={"source_file": "notes/test.md", "folder_path": "notes",
                      "filename": "test.md", "chunk_index": 0, "chunk_total": 1,
                      "word_count": 2, "is_table": False, "is_code": False,
                      "heading_breadcrumb": "", "last_indexed": ""},
        )
        mock_store = MagicMock()
        mock_store.all_metadata.return_value = [{"source_file": "notes/test.md"}]
        mock_engine = MagicMock()
        mock_llm = MagicMock()
        mock_llm.synthesise.return_value = "Synthesised answer."
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [mock_chunk]

        with (
            patch("mdcore.serve.chain.VectorStore", return_value=mock_store),
            patch("mdcore.serve.chain.EmbeddingEngine", return_value=mock_engine),
            patch("mdcore.serve.chain.LLMLayer", return_value=mock_llm),
            patch("mdcore.serve.chain.KeywordPreFilter") as mock_pf_cls,
            patch("mdcore.serve.chain.VectorSearcher", return_value=mock_searcher),
            patch("mdcore.serve.chain.group_by_source") as mock_group,
            patch("mdcore.serve.chain.stitch") as mock_stitch,
            patch("mdcore.serve.chain.rank_sources") as mock_rank,
            patch("mdcore.serve.chain.assemble") as mock_assemble,
            patch("mdcore.serve.chain.format_context", return_value="ctx"),
        ):
            mock_pf_cls.return_value.filter.return_value = None
            mock_group.return_value = {"notes/test.md": [mock_chunk]}
            mock_stitch.return_value = [mock_chunk]
            mock_rank.return_value = [("notes/test.md", [mock_chunk])]
            assembled = MagicMock()
            assembled.primary = [("notes/test.md", [mock_chunk])]
            mock_assemble.return_value = assembled

            from mdcore.serve.chain import build_search_chain
            chain = build_search_chain(minimal_cfg)
            result = chain.invoke({"query": "mTLS topology"})

        assert result["answer"] == "Synthesised answer."
        assert "notes/test.md" in result["sources"]

from __future__ import annotations
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableLambda

from mdcore.config.models import MdCoreConfig
from mdcore.core.indexer.embedding_engine import EmbeddingEngine
from mdcore.core.retriever.vector_searcher import VectorSearcher
from mdcore.core.retriever.keyword_prefilter import KeywordPreFilter
from mdcore.core.retriever.context_assembler import assemble
from mdcore.core.retriever.context_formatter import format_context
from mdcore.core.retriever.chunk_grouper import group_by_source
from mdcore.core.retriever.chunk_stitcher import stitch
from mdcore.core.retriever.source_ranker import rank_sources
from mdcore.llm.llm_layer import LLMLayer
from mdcore.store.vector_store import VectorStore


def build_search_chain(cfg: MdCoreConfig):
    """
    Build a LangChain Runnable wrapping the mdcore retrieval pipeline.

    Input:  {"query": str}
    Output: {"answer": str, "sources": list[str], "raw_context": str}
    """
    embedding_engine = EmbeddingEngine(cfg.embeddings)
    vector_store = VectorStore(cfg.vector_store)
    llm_layer = LLMLayer(cfg.llm)
    prefilter = KeywordPreFilter(
        min_score=cfg.retriever.keyword_prefilter_min_score,
        owner_name=cfg.vault.owner_name,
    )
    searcher = VectorSearcher(vector_store, embedding_engine, cfg.retriever)

    def retrieve_and_synthesise(inputs: dict[str, Any]) -> dict[str, Any]:
        query = inputs["query"]

        all_meta = vector_store.all_metadata()
        candidate_sources = prefilter.filter(query, all_meta) if cfg.retriever.keyword_prefilter else None

        chunks = searcher.search(query, candidate_sources)

        if not chunks:
            return {
                "answer": "No relevant content found in the vault for this query.",
                "sources": [],
                "raw_context": "",
            }

        grouped = group_by_source(chunks)
        stitched = {sf: stitch(sf, ps, cfg.retriever) for sf, ps in grouped.items()}
        ranked = rank_sources(stitched)
        assembled = assemble(query, ranked, cfg.retriever)
        raw_context = format_context(assembled, cfg.retriever)

        answer = llm_layer.synthesise(query, raw_context)
        sources = [sf for sf, _ in assembled.primary]

        return {
            "answer": answer,
            "sources": sources,
            "raw_context": raw_context,
        }

    return RunnableLambda(retrieve_and_synthesise)


def build_ingest_chain(cfg: MdCoreConfig):
    """
    Build a LangChain Runnable wrapping the mdcore ingestion pipeline.

    Input:  {"content": str, "title": str}
    Output: {"status": str, "action": str, "target_file": str|None,
             "suggested_folder": str|None, "proposal": str, "confidence": float}

    Returns a proposal only - does NOT write to disk. Mirrors CLI require_approval=True.
    """
    from mdcore.core.ingester.summary_embedder import SummaryEmbedder
    from mdcore.core.ingester.classification_engine import ClassificationEngine
    from mdcore.core.ingester.folder_router import FolderRouter
    from mdcore.core.ingester.proposal_generator import ProposalGenerator

    embedding_engine = EmbeddingEngine(cfg.embeddings)
    vector_store = VectorStore(cfg.vector_store)
    llm_layer = LLMLayer(cfg.llm)

    def classify_and_propose(inputs: dict[str, Any]) -> dict[str, Any]:
        content = inputs["content"]
        title = inputs.get("title", "Untitled")

        embedder = SummaryEmbedder(embedding_engine)
        summary_embeddings = embedder.embed(content)

        # 3 args: store, llm, cfg (no embedding_engine per actual source)
        classifier = ClassificationEngine(vector_store, llm_layer, cfg.ingester)
        # embedding first, text second (actual signature order)
        decision = classifier.classify(summary_embeddings.full, content)

        folder: str | None = None
        if decision.action == "new":
            # 3 separate config objects (not combined MdCoreConfig)
            router = FolderRouter(cfg.vault, cfg.ingester, llm_layer)
            folder, _ = router.route(content, decision.top_scores)

        existing_content = ""
        if decision.target_file:
            target_path = Path(cfg.vault.path).expanduser() / decision.target_file
            if target_path.exists():
                existing_content = target_path.read_text(encoding="utf-8", errors="ignore")[:600]

        # method is generate(), not propose(); returns Proposal dataclass
        proposer = ProposalGenerator(llm_layer)
        proposal = proposer.generate(
            decision=decision,
            incoming_summary=content[:800],
            existing_content=existing_content,
            suggested_folder=folder or "",
        )

        return {
            "status": "proposal_ready",
            "action": decision.action,
            "target_file": decision.target_file,
            "suggested_folder": folder,
            "proposal": proposal.proposal_text,
            "confidence": decision.confidence,
        }

    return RunnableLambda(classify_and_propose)

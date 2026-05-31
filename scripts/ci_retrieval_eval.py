#!/usr/bin/env python3
"""Offline retrieval-quality gate for CI.

Indexes a committed synthetic fixture vault with a free local embedding backend
(HuggingFace all-MiniLM-L6-v2 — no API key, no network beyond the model
download), runs each fixture query through the real mdcore retrieval pipeline
(BM25 hybrid pre-filter + vector search), and asserts that each query retrieves
its expected source note within the top-k.

This is deterministic and needs no LLM, so it can run on every pull request.
The richer LLM-synthesis + LLM-judge evaluation against a real vault lives in
scripts/langsmith_eval.py and stays a manual local tool.

If LANGSMITH_API_KEY is set in the environment, per-query results are also posted
to LangSmith as an experiment (best-effort; never fails the gate).

Usage:
    python scripts/ci_retrieval_eval.py
    python scripts/ci_retrieval_eval.py --min-recall 0.8

Exit code 0 if recall@k >= threshold, 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mdcore.config.loader import load_config
from mdcore.core.indexer.vault_scanner import VaultScanner
from mdcore.core.indexer.document_loader import DocumentLoader
from mdcore.core.indexer.text_splitter import TextSplitter
from mdcore.core.indexer.index_writer import IndexWriter
from mdcore.core.retriever.keyword_prefilter import KeywordPreFilter
from mdcore.core.retriever.vector_searcher import VectorSearcher
from mdcore.store.vector_store import VectorStore
from mdcore.core.indexer.embedding_engine import EmbeddingEngine

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "eval"
DATASET_NAME = "mdcore-ci-retrieval-fixture"
EXPERIMENT_PREFIX = "mdcore-ci-retrieval"


def _load_fixture_config():
    """Load the fixture config and pin all paths to absolute, repo-relative
    locations so the eval is independent of the developer's ~/.mdcore setup."""
    cfg = load_config(str(FIXTURE_DIR / "config.yaml"))
    # Override paths — never trust the developer's ~/.mdcore/models.yaml merge.
    cfg.vault.path = str(FIXTURE_DIR / "vault")
    cfg.embeddings.backend = "huggingface"
    cfg.embeddings.local_model = "sentence-transformers/all-MiniLM-L6-v2"
    cfg.vector_store.persist_path = str(REPO_ROOT / ".eval-index" / "chroma_db")
    cfg.manifest.path = str(REPO_ROOT / ".eval-index" / "manifest.json")
    return cfg


def _index_fixture(cfg) -> None:
    """Fresh index of the fixture vault (wipes any prior index first)."""
    persist = Path(cfg.vector_store.persist_path)
    if persist.exists():
        shutil.rmtree(persist)

    scanner = VaultScanner(cfg.vault, cfg.indexer)
    loader = DocumentLoader(cfg.vault)
    splitter = TextSplitter(cfg.indexer)
    store = VectorStore(cfg.vector_store)
    engine = EmbeddingEngine(cfg.embeddings)
    writer = IndexWriter(store, engine, cfg.indexer)

    eligible = scanner.scan()
    for path in eligible:
        doc = loader.load(path)
        chunks = splitter.split(doc)
        source_file = doc.metadata.get("source_file", str(path))
        writer.write(chunks, source_file)
    print(f"Indexed {len(eligible)} fixture notes.")


def _retrieve_sources(cfg, store, engine, query: str) -> set[str]:
    """Run the real pipeline (prefilter + vector search) and return the basenames
    of every retrieved source file."""
    candidate_sources = None
    if cfg.retriever.keyword_prefilter:
        prefilter = KeywordPreFilter(
            cfg.retriever.keyword_prefilter_min_score,
            owner_name=cfg.vault.owner_name,
            mode=cfg.retriever.keyword_prefilter_mode,
        )
        candidate_sources = prefilter.filter(query, store.all_chunks()) or None

    searcher = VectorSearcher(store, engine, cfg.retriever)
    chunks = searcher.search(query, candidate_sources)
    return {Path(c.metadata.get("source_file", "")).name for c in chunks}


def _load_questions() -> list[dict]:
    with open(FIXTURE_DIR / "questions.yaml") as f:
        return (yaml.safe_load(f) or {}).get("questions", [])


def _post_to_langsmith(rows: list[dict], recall: float) -> None:
    """Best-effort: post the run to LangSmith if a key is present. Never raises."""
    api_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    if not api_key:
        print("LANGSMITH_API_KEY not set — skipping LangSmith upload.")
        return
    try:
        from langsmith import Client
        from langsmith.evaluation import evaluate

        client = Client(api_key=api_key)

        # Recreate dataset each run so it always matches the fixture questions.
        existing = list(client.list_datasets(dataset_name=DATASET_NAME))
        if existing:
            client.delete_dataset(dataset_id=existing[0].id)
        ds = client.create_dataset(
            dataset_name=DATASET_NAME,
            description="mdcore CI offline retrieval gate — synthetic fixture vault",
        )
        client.create_examples(
            inputs=[{"query": r["query"]} for r in rows],
            outputs=[{"expected_source": r["expected"]} for r in rows],
            dataset_id=ds.id,
        )

        by_query = {r["query"]: r for r in rows}

        def target(inputs: dict) -> dict:
            r = by_query[inputs["query"]]
            return {"retrieved": sorted(r["retrieved"]), "hit": r["hit"]}

        def recall_evaluator(run, example) -> dict:
            return {"key": "retrieval_hit", "score": int((run.outputs or {}).get("hit", False))}

        evaluate(
            target,
            data=DATASET_NAME,
            evaluators=[recall_evaluator],
            experiment_prefix=EXPERIMENT_PREFIX,
            client=client,
            metadata={"recall_at_k": recall, "backend": "huggingface/all-MiniLM-L6-v2"},
        )
        project = os.environ.get("LANGSMITH_PROJECT", "mdcore")
        print(f"Posted experiment to LangSmith (project '{project}').")
    except Exception as exc:  # noqa: BLE001 — telemetry must never break the gate
        print(f"LangSmith upload skipped (non-fatal): {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline retrieval gate for CI")
    parser.add_argument("--min-recall", type=float, default=0.8,
                        help="Minimum recall@top_k required to pass (default 0.8)")
    args = parser.parse_args()

    cfg = _load_fixture_config()
    _index_fixture(cfg)

    store = VectorStore(cfg.vector_store)
    engine = EmbeddingEngine(cfg.embeddings)

    questions = _load_questions()
    if not questions:
        print("ERROR: no fixture questions found.")
        sys.exit(1)

    rows: list[dict] = []
    hits = 0
    for q in questions:
        query = q["query"]
        expected = q["expected_source"]
        retrieved = _retrieve_sources(cfg, store, engine, query)
        hit = expected in retrieved
        hits += int(hit)
        rows.append({"query": query, "expected": expected, "retrieved": retrieved, "hit": hit})

    total = len(questions)
    recall = hits / total if total else 0.0

    print(f"\n--- Retrieval gate (top_k={cfg.retriever.top_k}, mode={cfg.retriever.keyword_prefilter_mode}) ---")
    for r in rows:
        mark = "PASS" if r["hit"] else "FAIL"
        print(f"  [{mark}] {r['expected']:<28} <- {r['query'][:50]!r}")
        if not r["hit"]:
            print(f"         retrieved instead: {sorted(r['retrieved'])}")
    print(f"\nrecall@{cfg.retriever.top_k}: {recall:.2f}  ({hits}/{total})  threshold: {args.min_recall:.2f}")

    _post_to_langsmith(rows, recall)

    if recall < args.min_recall:
        print(f"\nFAILED: recall {recall:.2f} < {args.min_recall:.2f}")
        sys.exit(1)
    print("\nPASSED.")


if __name__ == "__main__":
    main()

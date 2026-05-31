#!/usr/bin/env python3
"""
LangSmith retrieval quality evaluation for mdcore.

Runs 25 real vault queries through the search pipeline, scores each result
with three evaluators, and posts everything to LangSmith.

Usage:
    python scripts/langsmith_eval.py
    python scripts/langsmith_eval.py --questions scripts/eval_questions.yaml
    python scripts/langsmith_eval.py --recreate-dataset   # wipe and rebuild dataset

Requirements:
    pip install langsmith pyyaml
    langsmith_api_key set in ~/.mdcore/config.yaml
    vault indexed (mdcore index)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

# Ensure repo root is on path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from langsmith import Client
from langsmith.evaluation import evaluate

from mdcore.config.loader import load_config
from mdcore.serve.chain import build_search_chain

DEFAULT_QUESTIONS = Path(__file__).parent / "eval_questions.yaml"
DEFAULT_DATASET = "mdcore-eval-v1"
EXPERIMENT_PREFIX = "mdcore-retrieval"


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------

def eval_non_empty(run, example) -> dict:
    """Rule-based: did the pipeline return any answer at all?"""
    answer = (run.outputs or {}).get("answer", "")
    passed = bool(answer.strip()) and "No relevant content found" not in answer
    return {"key": "non_empty", "score": int(passed)}


def eval_has_sources(run, example) -> dict:
    """Rule-based: did the answer cite at least one source file?"""
    sources = (run.outputs or {}).get("sources", [])
    return {"key": "has_sources", "score": int(len(sources) > 0)}


def eval_relevance(run, example) -> dict:
    """
    LLM-as-judge: does the answer actually address the query?

    Reuses the mdcore LLM so no extra API key is needed.
    Score: 1 = relevant, 0 = not relevant.
    """
    try:
        from langchain.evaluation import load_evaluator

        llm = eval_relevance._llm  # type: ignore[attr-defined]
        evaluator = load_evaluator("relevance", llm=llm)

        query = (example.inputs or {}).get("query", "")
        answer = (run.outputs or {}).get("answer", "")

        if not answer.strip() or not query.strip():
            return {"key": "relevance", "score": 0}

        result = evaluator.evaluate_strings(prediction=answer, input=query)
        raw = result.get("score", result.get("value", 0))
        if isinstance(raw, str):
            score = 1 if raw.upper() in ("Y", "YES", "RELEVANT") else 0
        else:
            score = int(float(raw) >= 0.5)
        return {"key": "relevance", "score": score}

    except Exception as exc:
        return {"key": "relevance", "score": 0, "comment": f"judge error: {exc}"}


# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------

def load_questions(path: Path) -> list[dict]:
    with open(path) as f:
        data = yaml.safe_load(f)
    questions = data.get("questions", [])
    placeholders = [q for q in questions if "REPLACE ME" in q.get("query", "")]
    if placeholders:
        print(
            f"\nERROR: {len(placeholders)} question(s) still contain 'REPLACE ME'.\n"
            f"Open {path} and replace every placeholder with a real vault query.\n"
        )
        sys.exit(1)
    return questions


def get_or_create_dataset(client: Client, name: str, questions: list[dict], recreate: bool):
    existing = list(client.list_datasets(dataset_name=name))
    if existing and not recreate:
        print(f"Using existing dataset '{name}' ({existing[0].id})")
        return existing[0]
    if existing and recreate:
        client.delete_dataset(dataset_id=existing[0].id)
        print(f"Deleted existing dataset '{name}', recreating...")
    dataset = client.create_dataset(
        dataset_name=name,
        description="mdcore retrieval quality evaluation - real vault queries",
    )
    client.create_examples(
        inputs=[{"query": q["query"]} for q in questions],
        outputs=[{"expected_topics": q.get("expected_topics", [])} for q in questions],
        dataset_id=dataset.id,
    )
    print(f"Created dataset '{name}' with {len(questions)} questions.")
    return dataset


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run mdcore LangSmith eval")
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET)
    parser.add_argument("--recreate-dataset", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)

    if not cfg.llm.langsmith_api_key:
        print(
            "\nERROR: langsmith_api_key not set in config.\n"
            "Add to ~/.mdcore/config.yaml:\n\n"
            "  llm:\n"
            "    langsmith_api_key: ls-...\n"
            "    langsmith_project: mdcore\n"
        )
        sys.exit(1)

    client = Client(api_key=cfg.llm.langsmith_api_key)

    questions = load_questions(Path(args.questions))
    print(f"Loaded {len(questions)} questions from {args.questions}")

    get_or_create_dataset(client, args.dataset_name, questions, args.recreate_dataset)

    print("\nInitialising mdcore search pipeline...")
    chain = build_search_chain(cfg)

    from mdcore.llm.llm_layer import LLMLayer
    llm_layer = LLMLayer(cfg.llm)
    eval_relevance._llm = llm_layer._get_llm()  # type: ignore[attr-defined]

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return chain.invoke(inputs)

    print(f"Running eval against dataset '{args.dataset_name}'...")
    print("One LLM call per question for synthesis + one for relevance judging.\n")

    results = evaluate(
        target,
        data=args.dataset_name,
        evaluators=[eval_non_empty, eval_has_sources, eval_relevance],
        experiment_prefix=EXPERIMENT_PREFIX,
        client=client,
        metadata={"mdcore_version": "1.2.0", "vault": cfg.vault.path},
    )

    experiment_name = getattr(results, "experiment_name", None)
    scores: dict[str, list[float]] = {}
    if experiment_name:
        run_ids = [str(r.id) for r in client.list_runs(project_name=experiment_name, run_type="chain")]
        for fb in client.list_feedback(run_ids=run_ids, feedback_key=["non_empty", "has_sources", "relevance"]):
            scores.setdefault(fb.key, []).append(fb.score or 0)

    print("\n--- Results ---")
    if scores:
        for key, vals in scores.items():
            pct = sum(vals) / len(vals) * 100 if vals else 0
            print(f"  {key:<20} {pct:5.1f}%  ({int(sum(vals))}/{len(vals)})")
    else:
        print("  (check LangSmith UI — score parsing requires experiment name)")

    project = cfg.llm.langsmith_project or "mdcore"
    print(f"\nFull traces: https://smith.langchain.com/o/default/projects/p/{project}")
    print("Screenshot the Experiments tab for your records.")


if __name__ == "__main__":
    main()

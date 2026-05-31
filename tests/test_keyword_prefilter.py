"""Tests for the BM25 content-aware keyword pre-filter.

The pre-filter selects candidate source files before vector search. It supports
three modes:
  - "path":   legacy behaviour — term-presence ratio over filename + folder_path
  - "bm25":   BM25 lexical scoring over chunk *content*
  - "hybrid": union of bm25 content matches and path matches (default)

Owner-aware logic (cross-person contamination guard) applies in every mode:
when the vault owner's name appears in the query, files under another person's
folder are excluded from candidacy.
"""

from __future__ import annotations

from langchain_core.documents import Document

from mdcore.core.retriever.keyword_prefilter import KeywordPreFilter


def _chunk(source_file: str, text: str, *, filename: str = "", folder: str = "") -> Document:
    """Build a chunk Document the way the vault loader/splitter would."""
    return Document(
        page_content=text,
        metadata={
            "source_file": source_file,
            "filename": filename or source_file.split("/")[-1],
            "folder_path": folder,
        },
    )


# ── BM25 content scoring ──────────────────────────────────────────────────────

def test_bm25_ranks_content_match_over_path_only_miss():
    """A file whose CONTENT matches the query is selected even when its name does not."""
    chunks = [
        _chunk("notes/kubernetes-setup.md",
               "Configure pod to pod communication using the CNI plugin and service mesh.",
               filename="kubernetes-setup.md", folder="notes"),
        _chunk("notes/grocery-list.md",
               "Milk eggs bread butter coffee and apples for the week.",
               filename="grocery-list.md", folder="notes"),
    ]
    pf = KeywordPreFilter(mode="bm25")

    result = pf.filter("CNI service mesh", chunks)

    assert "notes/kubernetes-setup.md" in result
    assert "notes/grocery-list.md" not in result


def test_bm25_aggregates_multiple_chunks_per_file():
    """Per-file score is the best matching chunk; a strong chunk wins even if others miss."""
    chunks = [
        _chunk("a.md", "totally unrelated filler content here", folder=""),
        _chunk("a.md", "detailed discussion of postgres replication and WAL shipping", folder=""),
        _chunk("b.md", "weather notes and gardening tips", folder=""),
    ]
    pf = KeywordPreFilter(mode="bm25")

    result = pf.filter("postgres replication WAL", chunks)

    assert result == {"a.md"}


def test_bm25_no_content_match_returns_empty():
    """No query term in any chunk → empty set (caller falls back to unfiltered vector search)."""
    chunks = [
        _chunk("a.md", "cats and dogs", folder=""),
        _chunk("b.md", "rivers and mountains", folder=""),
    ]
    pf = KeywordPreFilter(mode="bm25")

    assert pf.filter("kubernetes helm chart", chunks) == set()


# ── Owner-aware filtering (applies across modes) ──────────────────────────────

def test_owner_query_excludes_other_person_folder():
    """When owner name is in the query, another person's folder is excluded even on content match."""
    chunks = [
        _chunk("Piyush/Career/resume.md",
               "salary negotiation and compensation strategy notes",
               filename="resume.md", folder="Piyush/Career"),
        _chunk("Aishwarya/Career/resume.md",
               "salary negotiation and compensation strategy notes",
               filename="resume.md", folder="Aishwarya/Career"),
    ]
    pf = KeywordPreFilter(mode="hybrid", owner_name="Piyush")

    result = pf.filter("piyush salary negotiation", chunks)

    assert "Piyush/Career/resume.md" in result
    assert "Aishwarya/Career/resume.md" not in result


def test_all_terms_were_owner_returns_all_sources():
    """If nothing remains after stripping the owner name, fall back to all sources."""
    chunks = [
        _chunk("a.md", "content one", folder=""),
        _chunk("b.md", "content two", folder=""),
    ]
    pf = KeywordPreFilter(mode="hybrid", owner_name="Piyush")

    result = pf.filter("piyush", chunks)

    assert result == {"a.md", "b.md"}


# ── Path mode regression (legacy behaviour preserved) ─────────────────────────

def test_path_mode_matches_on_filename_and_folder_only():
    """mode='path' reproduces the legacy term-presence ratio over filename + folder."""
    chunks = [
        _chunk("Projects/quarterly-review.md",
               "body text that does not contain the query words at all",
               filename="quarterly-review.md", folder="Projects"),
        _chunk("Misc/random.md", "quarterly review happened here in the body",
               filename="random.md", folder="Misc"),
    ]
    pf = KeywordPreFilter(min_score=0.3, mode="path")

    result = pf.filter("quarterly review", chunks)

    # path mode keys off filename/folder, not content
    assert "Projects/quarterly-review.md" in result
    assert "Misc/random.md" not in result


def test_hybrid_unions_path_and_content_matches():
    """hybrid selects a file matched by content AND a file matched by path."""
    chunks = [
        _chunk("Infra/docker-notes.md",
               "body about unrelated topics with no query terms",
               filename="docker-notes.md", folder="Infra"),
        _chunk("Misc/notes.md",
               "deep dive into kubernetes ingress controllers and load balancing",
               filename="notes.md", folder="Misc"),
    ]
    pf = KeywordPreFilter(mode="hybrid")

    result = pf.filter("docker kubernetes", chunks)

    assert "Infra/docker-notes.md" in result      # matched on path ("docker")
    assert "Misc/notes.md" in result               # matched on content ("kubernetes")


def test_empty_corpus_returns_empty():
    pf = KeywordPreFilter(mode="hybrid")
    assert pf.filter("anything", []) == set()

from __future__ import annotations

import re
from typing import Literal

from langchain_core.documents import Document

from mdcore.utils.logging import get_logger

log = get_logger("retriever.prefilter")

# Common English words that happen to start with an uppercase letter in folder
# names but are NOT person names. These are excluded when detecting whether a
# folder's first path component is a person's name.
_COMMON_FOLDER_WORDS = {
    "career", "learning", "misc", "notes", "projects", "personal",
    "programming", "noise", "emigration", "clippings", "related",
    "archive", "archives", "reading", "prep", "project", "annexes",
    "resources", "documents", "files", "work", "life", "private",
    "public", "inbox", "drafts", "templates", "reference", "areas",
    "daily", "weekly", "journal", "logs", "tasks", "todos",
}

# Score multiplier applied to files in other-person folders when the owner's
# name appears in the query.  0.2 drops a perfect-match score of 1.0 to 0.2,
# which falls below the default min_score of 0.3, effectively excluding them.
_OTHER_PERSON_PENALTY = 0.2

# Tokeniser for BM25: lowercase alphanumeric runs. Shared by corpus + query so
# that scoring is symmetric.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# When BM25 produces a large candidate set, keep only the top-N highest-scoring
# files. This is where BM25's ranking earns its keep — in a small vault every
# lexical match is returned; in a large one BM25 prioritises the most relevant.
_BM25_MAX_CANDIDATE_FILES = 50

PrefilterMode = Literal["hybrid", "bm25", "path"]


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _looks_like_person_name(word: str) -> bool:
    """Return True if word is plausibly a person's first name.

    Heuristic: starts with uppercase, not all-uppercase (rules out acronyms),
    purely alphabetic, 3–20 chars, not in the common folder words list.
    """
    if not (3 <= len(word) <= 20):
        return False
    if not word[0].isupper():
        return False
    if word.isupper():          # ALL-CAPS → acronym (AMEX, CBS, LBG…)
        return False
    if not word.isalpha():      # hyphens, digits → not a name
        return False
    if word.lower() in _COMMON_FOLDER_WORDS:
        return False
    return True


class KeywordPreFilter:
    """Selects candidate source files before vector search.

    Three modes:
      - "path":   legacy term-presence ratio over filename + folder_path only.
      - "bm25":   BM25 lexical scoring over chunk *content* (uses rank_bm25).
      - "hybrid": union of bm25 content matches and path matches (default).

    Across all modes, when the vault owner's name appears in the query, files
    under another person's folder are excluded (cross-person contamination
    guard).
    """

    def __init__(
        self,
        min_score: float = 0.3,
        owner_name: str = "",
        mode: PrefilterMode = "hybrid",
    ) -> None:
        self._min_score = min_score
        self._mode = mode
        # Support multi-word names ("Piyush Tyagi") — any word triggers the logic
        self._owner_words = {w.lower() for w in owner_name.split() if w}

    def filter(self, query: str, chunks: list[Document]) -> set[str]:
        """Return the set of candidate source_file values for `query`.

        `chunks` is the full set of indexed chunk Documents (page_content +
        metadata with source_file / filename / folder_path). BM25 scoring reads
        page_content; path scoring reads filename + folder_path.
        """
        if not chunks:
            return set()

        raw_terms = set(query.lower().split())

        # ── Persona detection ─────────────────────────────────────────────────
        owner_in_query = bool(self._owner_words and self._owner_words & raw_terms)
        other_person_prefixes: set[str] = set()

        if owner_in_query:
            # Strip owner words from keyword terms — they add no signal.
            terms = raw_terms - self._owner_words

            # Collect the first WORD of the first path component that looks like
            # a person's name but is not the vault owner.
            for meta in (c.metadata for c in chunks):
                folder_orig = meta.get("folder_path", "")
                if not folder_orig:
                    continue
                first_component = folder_orig.replace("\\", "/").split("/")[0].strip()
                first_word = first_component.split()[0] if first_component else ""
                if (_looks_like_person_name(first_word)
                        and first_word.lower() not in self._owner_words):
                    other_person_prefixes.add(first_component.lower())

            if other_person_prefixes:
                log.debug(
                    "Owner query detected — excluding other-person folders: %s",
                    other_person_prefixes,
                )
        else:
            terms = raw_terms

        # All terms were the owner's name — nothing left to match on; fall back
        # to returning every source (let vector search decide).
        if not terms:
            log.debug("KeywordPreFilter: no terms remain after owner strip — returning all sources")
            return {c.metadata.get("source_file", "") for c in chunks}

        def _is_other_person(folder: str) -> bool:
            if not other_person_prefixes:
                return False
            first_component = folder.replace("\\", "/").split("/")[0].strip().lower()
            return first_component in other_person_prefixes

        candidates: set[str] = set()

        # ── BM25 content scoring (bm25 + hybrid) ────────────────────────────────
        if self._mode in ("bm25", "hybrid"):
            candidates |= self._bm25_candidates(terms, chunks, _is_other_person)

        # ── Path / filename scoring (path + hybrid) ─────────────────────────────
        if self._mode in ("path", "hybrid"):
            candidates |= self._path_candidates(terms, chunks, _is_other_person)

        log.debug(
            "KeywordPreFilter[%s]: %d candidate sources for '%s' (owner_in_query=%s)",
            self._mode, len(candidates), query, owner_in_query,
        )
        return candidates

    # ── scoring strategies ────────────────────────────────────────────────────

    def _bm25_candidates(self, terms, chunks, is_other_person) -> set[str]:
        """BM25 over chunk content.

        Candidacy is gated on lexical token overlap (robust at any vault size —
        BM25's IDF can be zero or negative for terms present in a large share of
        a tiny corpus, so a raw score threshold would wrongly drop them). BM25
        then ranks the matching files, and the set is capped to the top-N most
        relevant. Other-person folders are excluded when the owner name was in
        the query.
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            log.warning(
                "rank_bm25 not installed — BM25 pre-filter disabled. "
                "Install with: pip install rank-bm25"
            )
            return set()

        query_tokens = {tok for term in terms for tok in _tokenize(term)}
        if not query_tokens:
            return set()

        tokenized_corpus: list[list[str]] = []
        sources: list[str] = []
        for c in chunks:
            if is_other_person(c.metadata.get("folder_path", "")):
                continue
            tokenized_corpus.append(_tokenize(c.page_content))
            sources.append(c.metadata.get("source_file", ""))

        if not tokenized_corpus:
            return set()

        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(list(query_tokens))

        # Per-file best BM25 score, but only over chunks that lexically overlap
        # the query — that overlap is the candidacy gate.
        best_by_file: dict[str, float] = {}
        for tokens, sf, score in zip(tokenized_corpus, sources, scores):
            if query_tokens & set(tokens):
                if score > best_by_file.get(sf, float("-inf")):
                    best_by_file[sf] = score

        if len(best_by_file) <= _BM25_MAX_CANDIDATE_FILES:
            return set(best_by_file)

        ranked = sorted(best_by_file, key=best_by_file.get, reverse=True)
        return set(ranked[:_BM25_MAX_CANDIDATE_FILES])

    def _path_candidates(self, terms, chunks, is_other_person) -> set[str]:
        """Legacy term-presence ratio over filename + folder_path."""
        matching: set[str] = set()
        term_list = list(terms)
        for c in chunks:
            meta = c.metadata
            sf = meta.get("source_file", "")
            filename = meta.get("filename", "").lower()
            folder = meta.get("folder_path", "").lower()
            target = filename + " " + folder

            score = sum(1 for t in term_list if t in target) / len(term_list)

            if is_other_person(folder):
                score *= _OTHER_PERSON_PENALTY

            if score >= self._min_score:
                matching.add(sf)
        return matching

## mdcore/core/retriever/ — Retriever Module

### Purpose
Implements Flow A retrieval: keyword pre-filtering, vector search, chunk grouping, stitching adjacent chunks into passages, ranking sources, assembling a word-budgeted context block, and formatting it for either raw output or LLM synthesis input.

### Public interface

**`KeywordPreFilter(min_score: float = 0.3, owner_name: str = "", mode: "hybrid"|"bm25"|"path" = "hybrid")`**
- `filter(query: str, chunks: list[Document]) -> set[str]` — returns candidate source_file paths
- Three modes:
  - **`bm25`** — BM25 (`rank_bm25.BM25Okapi`) over chunk *content*. Candidacy is gated on lexical token overlap (robust at any vault size — BM25 IDF can be zero/negative for terms common in a tiny corpus, so a raw score threshold would wrongly drop them); BM25 then ranks matching files and the set is capped at `_BM25_MAX_CANDIDATE_FILES` (50). Per-file score = best matching chunk.
  - **`path`** — legacy term-presence ratio over filename + folder_path: `sum(1 for t in terms if t in (filename + " " + folder_path)) / len(terms)`, threshold `min_score` (0.3).
  - **`hybrid`** (default) — union of `bm25` content matches and `path` matches.
- Owner name logic (applies in every mode):
  1. If owner_name words appear in query: strip them from terms
  2. Detect "other-person" folder prefixes: first word of first path component that is a plausible person name (uppercase, alpha, 3-20 chars, not in _COMMON_FOLDER_WORDS list, not owner)
  3. Exclude files under those folders from candidacy (cross-person contamination guard; path mode additionally applies a 0.2x penalty)
  4. If all terms were owner words (nothing left): return ALL source_files (fall back to vector search)
- Reads: `page_content` (bm25) and filename/folder_path (path) from chunk Documents — fetched via `VectorStore.all_chunks()`

**`VectorSearcher(store: VectorStore, engine: EmbeddingEngine, cfg: RetrieverConfig)`**
- `search(query: str, candidate_sources: set[str] | None) -> list[Document]`
- Phase 1: embed_query(query) -> store.search(emb, k=top_k*2 if candidates else top_k) -> filter to candidates -> trim to top_k -> filter by similarity_threshold (0.65)
- Phase 2 (rescue): if candidate_sources provided, find sources with zero chunks in phase 1 -> store.search_in_sources(emb, missing, k=top_k) at 0.75 x threshold
- Returns list[Document] with `_similarity` in metadata

**`group_by_source(chunks: list[Document]) -> dict[str, list[Document]]`** (module-level function)
- Groups by `metadata["source_file"]`, sorts each group by `chunk_index`

**`stitch(source_file: str, chunks: list[Document], cfg: RetrieverConfig) -> list[StitchedPassage]`** (module-level function)
- Joins adjacent/near-adjacent chunks (gap <= stitch_distance=2 in chunk_index units)
- Gap filling: if a non-retrieved chunk sits between two retrieved chunks within stitch_distance, the group extends through it using retrieved-chunk text only (gap chunks not fetched from store — only in-memory retrieved chunks used for text)
- Truncates at stitch_max_words (400): appends " ..."
- Returns list[StitchedPassage]: source_file, text, breadcrumbs (deduplicated), chunk_indices, word_count, avg_similarity

**`rank_sources(passages_by_source: dict[str, list[StitchedPassage]]) -> list[tuple[str, list[StitchedPassage]]]`** (module-level function)
- Sorts sources by mean avg_similarity of their passages, descending

**`assemble(query: str, ranked: list, cfg: RetrieverConfig) -> AssembledContext`** (module-level function)
- Applies word budget (context_block_max_words=1000)
- Per source: cap passages at max_chunks_per_source (2), check total word count fits remaining budget
- If source fits entirely: add to primary
- If source fits partially (remaining > 100): truncate passages to fit -> add to primary
- If source doesn't fit (remaining <= 100): add to signpost (up to signpost_max_items=8)
- Returns AssembledContext: query, primary (source, passages) list, signpost list, total_words, source_count

**`format_context(ctx: AssembledContext, cfg: RetrieverConfig) -> str`** (module-level function)
- Produces markdown context block for raw output or display
- Each primary source: `### [N] source_file\n*Sections: breadcrumbs*\n\npassage text\n\n---`
- Signpost section: markdown table with suggested `mdcore search` commands
- UNCLEAR: `cfg.output_format`, `cfg.include_similarity_scores`, `cfg.signpost_include_section_hints` are read from config but not used in this function

**`raw_text_for_synthesis(ctx: AssembledContext) -> str`** (module-level function)
- Produces numbered `[N] Source: path | Sections: breadcrumbs\ntext` blocks
- Caps total at 4000 chars (truncates with `...[truncated]` mid-block)
- Used as input to LLMLayer.synthesise()

**`StitchedPassage`** (dataclass)
- source_file: str, text: str, breadcrumbs: list[str], chunk_indices: list[int], word_count: int, avg_similarity: float

**`AssembledContext`** (dataclass)
- query: str, primary: list[tuple[str, list[StitchedPassage]]], signpost: list[tuple[str, list[str]]], total_words: int, source_count: int

### Internal execution path (Flow A)

KeywordPreFilter.filter() -> VectorSearcher.search() -> group_by_source() -> stitch() per source -> rank_sources() -> assemble() -> format_context() OR raw_text_for_synthesis()

### Side effects
- ChromaDB reads (VectorStore.search, VectorStore.search_in_sources, VectorStore.all_metadata for prefilter)
- EmbeddingEngine calls (embed_query, possibly cache miss -> API call)
- No filesystem writes

### Gotchas
- KeywordPreFilter in `hybrid`/`bm25` mode scores chunk content via BM25, so a file matches on body text even when its name/folder don't. In `path` mode it falls back to filename + folder_path only (a file named "work.md" in "Projects" matches "projects work" but not "quarterly review" in its body).
- BM25 candidacy is gated on token overlap, not a raw score threshold: BM25 IDF goes to zero/negative for terms present in a large fraction of a small corpus, which would otherwise drop a file that clearly contains the term.
- Phase 2 rescue threshold = 0.75 x similarity_threshold. Default: 0.65 x 0.75 = 0.4875. Files keyword-matched but semantically distant still get included at this lower bar.
- stitch() gap-fills by checking `retrieved_indices` list only — gaps between chunk_indices are spanned, but non-retrieved chunks' text is not fetched from store. Only retrieved chunks' text appears in output.
- AssembledContext.primary uses actual passages from ranked list. If a source has 5 chunks but max_chunks_per_source=2, only 2 are used even if more fit the budget.
- raw_text_for_synthesis truncates at 4000 chars total — for local models with small context windows. API models with large contexts would benefit from a higher limit (not configurable).

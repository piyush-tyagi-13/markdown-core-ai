## mdcore/core/ingester/ — Ingester Module

### Purpose
Implements Flow B: classifying an incoming document, routing it to the right vault folder, detecting conflicts with existing content, generating a human-readable proposal, and triggering the write pipeline.

### Public interface

**`SummaryReceiver(cfg: IngesterConfig)`**
- `receive_from_file(path: str) -> str` — reads file, validates, returns text
- `receive_from_text(text: str) -> str` — validates text, returns stripped text
- Validation: raises `ValueError` if word_count < cfg.min_summary_word_count (100), or heading count < cfg.min_summary_headings (1)

**`SummaryEmbedder(engine: EmbeddingEngine)`**
- `embed(summary: str) -> SummaryEmbeddings` — embeds full text + per-sentence
- `SummaryEmbeddings.full: list[float]` — full document embedding
- `SummaryEmbeddings.sentences: list[tuple[str, list[float]]]` — (sentence, embedding) pairs
- Sentences: split by `. ! ?`, minimum 5 words per sentence

**`ClassificationEngine(store: VectorStore, llm: LLMLayer, cfg: IngesterConfig)`**
- `classify(summary_embedding: list[float], summary_text: str) -> ClassificationDecision`
- Three-zone logic:
  - Zone 1 (auto-UPDATE): top_score > 0.82 AND NOT _is_self_contained(summary_text) → no LLM call
  - Zone 2 (LLM): 0.65 ≤ top_score ≤ 0.82 → LLMLayer.classify() called
  - Zone 3 (auto-NEW): top_score < 0.65 → no LLM call
- `_is_self_contained(text)` heuristic: h2_count ≥ 2 AND (has_table OR list_items ≥ 3)
  - GOTCHA: regex `^#{1,2}\s+\S` matches BOTH H1 and H2 headings (not just H2)
- `ClassificationDecision.top_scores: dict[str, float]` — top-10 files by similarity, passed to FolderRouter
- GOTCHA: When LLM is called, candidates list has `Document(page_content=source_file_path)` — NOT actual document content. LLM sees only file paths, not snippets.

**`FolderRouter(vault_cfg: VaultConfig, cfg: IngesterConfig, llm: LLMLayer)`**
- `route(document: str, top_scores: dict | None) -> tuple[str, float]` — (folder, confidence)
- Stage 1: if max_sim ≥ 0.60, derive candidate folders from top_scores (most specific matching vault folder per file, top 5 by sim)
- Always adds vault-map-described folders to candidates
- If max_sim < 0.60 or no candidates: use full vault folder list
- Stage 2: LLMLayer.route_folder(document, candidate_folders, descriptions)
- If LLM returns folder not in all_folders: retry with full folder list (1 extra LLM call)
- `needs_confirmation(confidence: float) -> bool` — True if confidence < cfg.folder_routing_confidence (0.75)
- Reads VaultMap from `<vault>/.mdcore-meta.yaml`
- GOTCHA: _get_folders() scans entire vault on every route() call — no caching

**`ConflictDetector(engine: EmbeddingEngine, cfg: IngesterConfig)`**
- `detect(existing_text: str, incoming_text: str) -> list[ConflictPair]`
- Splits both texts into sentences (5+ words, split on `.!?`)
- Embeds all sentences (may be many API calls for long documents)
- O(n×m) comparison: for each incoming sentence, compare vs all existing sentences
- Returns pairs where cfg.conflict_similarity_min (0.88) ≤ similarity ≤ cfg.conflict_similarity_max (0.97)
- Capped at 10 pairs to avoid overwhelming the proposal
- If cfg.conflict_detection=False: returns []
- Conflict is FLAG ONLY — does NOT block write

**`ProposalGenerator(llm: LLMLayer)`**
- `generate(decision, incoming_summary, existing_content, conflicts, suggested_folder, frontmatter_updates) -> Proposal`
- Calls LLMLayer.propose() — always fires during ingest (no condition)
- `Proposal.proposal_text` — 2-4 bullet points from LLM
- `Proposal.conflicts` — list[ConflictPair] (from caller)
- `Proposal.frontmatter_updates` — dict for FrontmatterInjector

### Internal execution path (Flow B)

1. SummaryReceiver validates incoming text (word count + headings)
2. SummaryEmbedder embeds text via EmbeddingEngine
3. ClassificationEngine:
   - Reads all file embeddings from VectorStore (full ChromaDB scan)
   - Computes cosine similarities (pure Python, no numpy)
   - Applies zone logic, optionally calls LLM
4. FolderRouter (if action=new):
   - Reads VaultMap descriptions
   - Scans vault for all directories
   - Derives candidate folders from top-k semantically matching files
   - Calls LLM to pick folder
5. ConflictDetector (if action=update):
   - Embeds all sentences of both documents
   - Pairwise comparison
6. ProposalGenerator:
   - Calls LLM.propose() to generate human-readable bullet points

### Side effects
- ChromaDB reads (file_embeddings, all_metadata)
- EmbeddingEngine calls (may update embed_cache.pkl)
- LLM calls (classify, route_folder, propose)
- Reads .mdcore-meta.yaml (FolderRouter via VaultMap)
- Reads vault filesystem for folder discovery (FolderRouter._get_folders())

### Gotchas
- ClassificationEngine.classify() is called with summary_embedding (not sentences embedding). Only the full-document embedding is compared against file mean embeddings.
- ConflictDetector can be slow for large documents (O(n×m) sentence comparisons with embedding calls).
- FolderRouter._get_folders() re-scans the entire vault on every call — not cached.
- LLM classify() candidates have page_content = source_file path, not actual content. LLM cannot see document excerpts.
- Conflict detection 0.88-0.97 window: pairs with similarity > 0.97 are treated as near-identical (not conflicts). Pairs < 0.88 are assumed different enough not to conflict.
- ConflictPair list capped at 10 — additional conflicts are silently dropped.

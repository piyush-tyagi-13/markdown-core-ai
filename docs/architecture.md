# mdcore Architecture

mdcore is a personal knowledge retrieval system built around a local markdown vault. It indexes notes, retrieves relevant context via semantic search, and can ingest new structured summaries into the vault. LangChain provides the LLM and embedding adapters; all retrieval, ranking, and routing logic is custom.

---

## Data Model

Every piece of content goes through this chain:

```
Vault file (.md / .pdf / .docx)
  → Document (file + frontmatter metadata)
  → Chunks (heading-aware splits, ~512 words each)
  → Embeddings (float vectors)
  → ChromaDB (persistent vector store)
  → Manifest (JSON: filepath → mtime, for drift detection)
```

---

## Flow 1: Indexing (`mdcore index`)

Scans the vault, computes what changed, and embeds only the delta.

```
VaultScanner
  → ManifestManager.diff()
  → DocumentLoader / MultiModalLoader
  → TextSplitter
  → EmbeddingEngine
  → IndexWriter → VectorStore (ChromaDB)
  → ManifestManager.save()
```

### Step by step

**1. VaultScanner** (`core/indexer/vault_scanner.py`)

Walks the vault directory recursively. For each file:
- Checks extension whitelist (`.md` always; `.pdf`, `.docx`, `.txt` if multimodal enabled)
- Checks minimum word count (default: 50)
- Validates structure signals via `markdown-it` parser (needs headings, paragraphs, or lists)

Output: list of eligible `Path` objects.

**2. ManifestManager** (`core/indexer/manifest_manager.py`)

Loads a JSON file (`manifest.json`) that maps `relative_path → mtime`. Compares against the scanner output to produce an `IndexDiff`:
- `new_files`: in vault, not in manifest
- `modified_files`: in both, but mtime changed
- `deleted_files`: in manifest, no longer in vault

Only the delta is reprocessed — unchanged files are skipped entirely.

**3. DocumentLoader / MultiModalLoader** (`core/indexer/`)

- **DocumentLoader** (`.md`): uses `python-frontmatter` to separate YAML frontmatter from body. Returns a LangChain `Document` with `page_content` (markdown body) and `metadata` (source path, folder, filename, frontmatter fields).
- **MultiModalLoader** (`.pdf`, `.docx`, `.txt`): extracts plain text, normalises into markdown-like sections. Returns same `Document` structure.

**4. TextSplitter** (`core/indexer/text_splitter.py`)

Custom splitter — does not use LangChain's text splitters.

- **Heading-aware mode** (default on): parses H2/H3 headings, builds a breadcrumb stack (e.g. `Performance > Memory Tuning`), groups text between headings into sections. Tables and code blocks are never split mid-content.
- **Token fallback**: if a section exceeds `chunk_size` words (default: 512), splits on word count with `chunk_overlap` (default: 64) word overlap.

Each output chunk is a `Document` enriched with:
- `heading_breadcrumb`: section hierarchy string
- `chunk_index`, `chunk_total`: position within the file
- `word_count`, `is_table`, `is_code`: content type flags
- `last_indexed`: ISO timestamp

**5. EmbeddingEngine** (`core/indexer/embedding_engine.py`)

Factory-builds a LangChain `Embeddings` instance based on config:

| Config backend | LangChain class | Notes |
|---|---|---|
| `ollama` | `OllamaEmbeddings` | local, free |
| `openai` | `OpenAIEmbeddings` | API key required |
| `gemini` | `GoogleGenerativeAIEmbeddings` | API key required |
| `huggingface` | `HuggingFaceEmbeddings` | local, free |

Caches vectors by SHA256 hash of chunk text. Truncates chunks to 6000 characters before embedding.

**6. IndexWriter → VectorStore** (`core/indexer/index_writer.py`, `core/indexer/vector_store.py`)

IndexWriter batches chunks (default: 32 per batch) and calls `VectorStore.upsert()`. VectorStore wraps ChromaDB directly (not via LangChain's Chroma wrapper):
- Single collection named `ctxkit_vault`
- Cosine distance metric
- Metadata sanitised to ChromaDB-compatible primitives (str, int, float, bool)
- Deleted files: `store.delete(source_file)` removes all chunks for that file before re-indexing

**7. ManifestManager.save()**

Writes updated `filepath → mtime` pairs. Next run uses this to skip unmodified files.

---

## Flow 2: Retrieval / Search (`mdcore search <query>`)

Turns a natural language query into a synthesised briefing with source citations.

```
KeywordPreFilter (optional heuristic)
  → VectorSearcher (embed query + similarity search)
  → ChunkGrouper (group by source file)
  → ChunkStitcher (bridge adjacent chunks)
  → SourceRanker (sort by mean similarity)
  → ContextAssembler (word budget enforcement)
  → ContextFormatter (markdown rendering)
  → LLMLayer.synthesise() (LLM briefing with citations)
```

### Step by step

**1. KeywordPreFilter** (`core/retriever/keyword_prefilter.py`)

Lexical pre-filter that runs before vector search, selecting candidate files. Default mode is `hybrid`:
- **BM25** (`rank_bm25.BM25Okapi`) over chunk *content* — files whose body lexically matches the query become candidates, ranked by BM25 and capped to the top 50
- **Path matching** — files whose filename/folder contain query terms (term-presence ratio ≥ `min_score`)
- `hybrid` takes the union of both; `bm25` and `path` modes use one signal only (config: `keyword_prefilter_mode`)
- When the owner name is in the query, files under another person's folder are excluded (cross-person contamination guard)

This reduces vector search scope and prevents cross-person retrieval contamination.

**2. VectorSearcher** (`core/retriever/vector_searcher.py`)

- Embeds the query via `engine.embed_query(query)`
- Searches ChromaDB for top-k chunks, filtered to candidate sources (from prefilter if enabled)
- Filters results by `similarity_threshold` (default: 0.65, cosine)
- **Keyword rescue phase**: for candidate files that returned zero chunks from semantic search (vocabulary mismatch), retries at a *relaxed* threshold of `similarity_threshold * 0.75` (e.g. 0.65 → ~0.49). The bar is lowered, not raised — a chunk scoring 0.55 fails phase 1 (< 0.65) but is rescued in phase 2 (≥ 0.49).

**3. ChunkGrouper** (`core/retriever/chunk_grouper.py`)

Groups chunks by `source_file`, sorts each group by `chunk_index`. Output: `dict[source_file → list[chunks]]`.

**4. ChunkStitcher** (`core/retriever/chunk_stitcher.py`)

Bridges gaps between non-consecutive retrieved chunks within a single source:
- If two retrieved chunks are within `stitch_distance` (default: 2) indices of each other, fills in the chunks between them
- Caps per-source chunks at `max_chunks_per_source + 2` to avoid context bloat
- Truncates stitched passages at `stitch_max_words` (default: 400)

Output per source: a `StitchedPassage` with joined text, merged breadcrumbs, and average similarity.

**5. SourceRanker** (`core/retriever/source_ranker.py`)

Sorts sources by mean similarity score of their passages, descending. Higher-similarity sources appear first in context.

**6. ContextAssembler** (`core/retriever/context_assembler.py`)

Enforces the word budget (`context_block_max_words`, default: 1000):
- Iterates ranked sources, includes passages until budget is exhausted
- Truncates the final passage if needed to fit exactly
- Sources that do not fit become "signpost" entries: breadcrumb hints + suggested follow-up queries only

**7. ContextFormatter** (`core/retriever/context_formatter.py`)

Renders assembled context as markdown:
- Numbered citations `[1]`, `[2]` per source
- Breadcrumb trail per source
- Signpost table if any sources were excluded from primary context
- Extracts raw text for synthesis prompt (numbered blocks format, capped at 4000 chars)

**8. LLMLayer.synthesise()** (`llm/llm_layer.py`)

Constructs a synthesis prompt instructing the LLM to:
- Rewrite excerpts into a coherent briefing
- Cite every claim with `[source_number]`
- Use only information present in the excerpts

Determines which LLM to use:
1. `synthesise_backend` + `synthesise_model` if explicitly configured
2. Primary backend + `synthesise_model` (different model, same provider)
3. Primary backend + primary model (full fallback)

Output written to `vault/mdcore-output/` as a markdown file.

---

## Flow 3: Ingestion (`mdcore ingest <file>`)

Takes a structured summary (markdown with headings) and decides whether to update an existing vault note or create a new one, then writes it.

```
SummaryReceiver (validate)
  → SummaryEmbedder (embed full + sentences)
  → ClassificationEngine (update vs new, via vector + LLM)
  → FolderRouter (if new: which folder?)
  → ConflictDetector (if update: overlapping sentences?)
  → ProposalGenerator → LLMLayer.propose()
  → [user approval]
  → Writer (backup + write + frontmatter + reindex)
```

### Step by step

**1. SummaryReceiver** (`core/ingester/summary_receiver.py`)

Validates the incoming summary: minimum word count (default: 100), minimum heading count (default: 1). Rejects bare text dumps.

**2. SummaryEmbedder** (`core/ingester/summary_embedder.py`)

- Embeds the full summary as a single vector (for file-level classification)
- Splits summary into sentences (split on `.!?`, minimum 5 words each)
- Embeds each sentence individually (for conflict detection later)

**3. ClassificationEngine** (`core/ingester/classification_engine.py`)

**Stage 1 — Vector similarity:**
- Gets mean embedding per indexed file from the vector store
- Computes cosine similarity between summary embedding and each file embedding
- **Self-containment check**: if the summary has 2+ H2 headings and includes tables or lists, treats it as a standalone document and forces "new"

**Decision thresholds:**
- Above `similarity_threshold_high` (0.82) → "update" the matched file
- Below `similarity_threshold_low` (0.65) → "new" file
- Between thresholds → ambiguous, call LLM

**Stage 2 — LLM disambiguation (ambiguous only):**
- Sends top-k candidate filenames + first 400 chars of each to the LLM
- LLM responds with `ACTION: update|new`, `TARGET: filepath|none`, `CONFIDENCE: 0.0-1.0`
- Parsed via regex from response text

**4. FolderRouter** (`core/ingester/folder_router.py`) — new files only

Decides which vault folder the new file should live in.

- **Semantic candidates**: extracts parent folders from top-k semantically similar files (if any exist above 0.60 similarity)
- **VaultMap overlay**: merges any folder descriptions from `.mdcore-meta.yaml` (user-authored descriptions)
- **LLM routing**: passes candidate folders with descriptions to the LLM, which picks the most appropriate one and is constrained to the provided list

Output: `(folder_path, confidence)`.

**5. ConflictDetector** (`core/ingester/conflict_detector.py`) — update files only

Detects near-duplicate sentences between the existing file and incoming summary:
- Computes pairwise cosine similarities across all sentence embeddings
- Flags pairs where similarity falls between `conflict_similarity_min` (0.88) and `conflict_similarity_max` (0.97) — this range targets restatements, not exact duplicates
- Returns top 10 conflict pairs

**6. ProposalGenerator → LLMLayer.propose()**

Calls `LLMLayer.propose()` with the classification result, existing file content (if update), and incoming summary. The LLM generates the exact text to write: insertion/replacement for "update", or a complete document for "new".

**7. Writer** (post-approval)

After user approves the proposal:
- **BackupManager**: timestamped copy of the existing file
- **FrontmatterInjector**: updates YAML frontmatter (tags, `updated` date, `related` links)
- **FileWriter**: appends or inserts content at configured position
- **IndexTrigger**: re-indexes only the modified file

---

## Flow 4: Vault Map (`mdcore map`)

Generates or updates `.mdcore-meta.yaml` — a human-editable YAML file describing what each vault folder is for. Used by FolderRouter during ingestion to improve routing accuracy.

```
VaultMap.all_vault_folders()
  → merge with existing descriptions
  → write .mdcore-meta.yaml template
  → [user edits]
  → FolderRouter reads descriptions on next ingest
```

**VaultMap** (`core/vault_map.py`):
- `all_vault_folders()`: recursively finds all subdirectories, excluding hidden and `mdcore-output/`
- `write_template()`: generates YAML with all folders, preserves existing descriptions, adds empty values for new folders
- `stale_descriptions()`: flags folders in metadata that no longer exist (for `--repair` mode)

Example `.mdcore-meta.yaml`:
```yaml
folders:
  Career: "Job applications, CV, interview prep"
  Career/Interviews: "Interview notes and preparation material"
  Personal Projects/mdcore: "mdcore development notes"
```

User fills in descriptions. On next `mdcore ingest`, FolderRouter passes these to the LLM alongside semantic candidates, improving routing for novel document types with no close semantic matches in the vault.

---

## Flow 5: Status (`mdcore status`)

Health check showing index freshness, drift, chunk count, and active LLM key.

```
VaultScanner.scan()
  → ManifestManager.diff()
  → VectorStore.all_metadata()
  → [if aggregator backend] AggregatorChat.current_key()
  → Rich table output
```

**What it reports:**
- Vault path and eligible file count
- Indexed file count and total chunk count
- Drift: new files not yet indexed, modified files with stale embeddings, deleted files still in index
- Warning if total drift >= `drift_warning_threshold` (default: 3)
- If `backend: aggregator`: active llm-keypool provider, model, request count, cooldown status

---

## LangChain: What It Does vs What mdcore Owns

mdcore uses LangChain as an adapter layer only.

| LangChain provides | mdcore implements custom |
|---|---|
| `Document` dataclass | Vault scanning and file eligibility |
| `Embeddings` base + backend subclasses | Heading-aware text splitting |
| `BaseChatModel` subclasses | Chunk stitching and passage ranking |
| `RunnableLambda` (LangSmith eval only) | Context assembly and word budgeting |
| | Keyword prefilter with owner-aware routing |
| | Conflict detection (sentence-level cosine) |
| | Folder routing heuristics + LLM prompting |
| | Prompt construction and response parsing |
| | Manifest-based incremental indexing |

---

## Config Reference

All behaviour tuned via `~/.mdcore/config.yaml` (overridden per-environment by `~/.mdcore/models.yaml`).

| Section | Key params |
|---|---|
| `indexer` | `chunk_size`, `chunk_overlap`, `min_word_count`, `heading_aware_splitting` |
| `embeddings` | `backend`, `api_model`, `api_key` |
| `retriever` | `top_k`, `similarity_threshold`, `context_block_max_words`, `keyword_prefilter` |
| `ingester` | `similarity_threshold_high`, `similarity_threshold_low`, `conflict_similarity_min/max` |
| `llm` | `backend`, `model`, `synthesise_model`, `fallback_backend`, `aggregator_category` |
| `writer` | `append_position`, `backup.enabled` |
| `manifest` | `drift_warning_threshold` |

---

## Component Map

```
mdcore/
├── cli/
│   └── commands.py                  CLI entrypoint for all commands
├── config/
│   ├── models.py                    Pydantic config models (all tuning params)
│   └── loader.py                    YAML loading + models.yaml merge
├── core/
│   ├── indexer/
│   │   ├── vault_scanner.py         File eligibility check
│   │   ├── manifest_manager.py      mtime-based drift detection
│   │   ├── document_loader.py       .md loader (frontmatter + body)
│   │   ├── multimodal_loader.py     PDF / DOCX / TXT loader
│   │   ├── text_splitter.py         Heading-aware + token chunker
│   │   ├── embedding_engine.py      LangChain embeddings wrapper + cache
│   │   ├── vector_store.py          ChromaDB wrapper
│   │   └── index_writer.py          Batch upsert orchestrator
│   ├── retriever/
│   │   ├── keyword_prefilter.py     Keyword + owner-aware candidate filter
│   │   ├── vector_searcher.py       Embed query + search + rescue phase
│   │   ├── chunk_grouper.py         Group chunks by source
│   │   ├── chunk_stitcher.py        Bridge adjacent chunk gaps
│   │   ├── source_ranker.py         Sort sources by similarity
│   │   ├── context_assembler.py     Word budget enforcement
│   │   └── context_formatter.py     Markdown rendering + synthesis input
│   ├── ingester/
│   │   ├── summary_receiver.py      Input validation
│   │   ├── summary_embedder.py      Full + sentence embeddings
│   │   ├── classification_engine.py Update vs new (vector + LLM)
│   │   ├── conflict_detector.py     Near-duplicate sentence detection
│   │   ├── folder_router.py         Vault map + LLM folder selection
│   │   └── proposal_generator.py    LLM write proposal
│   └── vault_map.py                 .mdcore-meta.yaml read/write
├── llm/
│   └── llm_layer.py                 LLM backend factory + classify/propose/synthesise
├── serve/
│   └── chain.py                     LangChain Runnable pipeline (for LangSmith eval)
└── mcp_server/
    └── server.py                    MCP stdio server (search_vault, index_vault tools)
```

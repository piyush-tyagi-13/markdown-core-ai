# ctxkit Config Reference

All configuration lives in `~/.ctxkit/config.yaml`. Use `ctxkit config` to open it in your editor and `ctxkit config --validate` to check for errors before running.

Multiple profiles are supported via the `--config` flag:

```bash
ctxkit search "topic" --config ~/.ctxkit/config-technical.yaml
ctxkit search "topic" --config ~/.ctxkit/config-personal.yaml
```

---

## vault

Controls which files ctxkit reads from and indexes.

```yaml
vault:
  path: /Users/you/obsidian-vault
  owner_name: ""
  excluded_folders:
    - noise
  excluded_extensions:
    - .canvas
    - .pdf
```

| Field | Type | Default | Description |
|---|---|---|---|
| `path` | string | — | **Required.** Absolute path to your markdown vault root. |
| `owner_name` | string | `""` | Your name as it appears in folder paths inside the vault (e.g. `"Piyush"`). When set, queries that mention your name are owner-aware: ctxkit strips your name from the vector query and penalises chunks from other people's top-level folders (detected by folder prefix casing heuristic). Leave blank for single-person vaults. |
| `excluded_folders` | list[string] | `["noise"]` | Folder names to skip entirely during scanning. Matched against any component of the path, so `noise` excludes `/vault/noise/` and `/vault/projects/noise/`. |
| `excluded_extensions` | list[string] | `[".canvas", ".pdf"]` | File extensions to skip. Must include the dot. |

**Tip:** Put low-quality notes, templates, and scratch files in a `noise` folder. The indexer also applies its own word count and structure filters as a secondary guard, but explicit exclusion is cleaner.

**Multi-person vaults:** If your vault contains subfolders for other people (e.g. a partner's career notes alongside yours), set `owner_name` to your first name. Queries like `"piyush career"` will then route to your files rather than theirs. ctxkit detects other-person folder prefixes by looking for Title-Case single words at the vault root that are not common folder names (`career`, `notes`, `misc`, etc.).

---

## indexer

Controls how files are chunked and what quality bar they must clear to be indexed.

```yaml
indexer:
  min_word_count: 50
  min_structure_signals: 1
  manifest_path: ~/.ctxkit/manifest.json
  chunk_size: 512
  chunk_overlap: 64
  max_chunk_words: 400
  heading_aware_splitting: true
  preserve_tables: true
  preserve_code_blocks: true
  heading_levels: [2, 3]
  batch_size: 32
  metadata_fields:
    - source_file
    - folder_path
    - filename
    - heading_breadcrumb
    - chunk_index
    - chunk_total
    - word_count
    - is_table
    - is_code
    - last_indexed
```

| Field | Type | Default | Description |
|---|---|---|---|
| `min_word_count` | int | `50` | Files with fewer words are skipped entirely. Also used as the minimum size for a heading section before it gets merged into the next sibling. Lower to `30` if you have short but valuable notes. |
| `min_structure_signals` | int | `1` | Minimum number of structural elements (headings, paragraphs, list items) required to index a file. Filters out pure attachment files or near-empty notes. |
| `manifest_path` | string | `~/.ctxkit/manifest.json` | Where the index manifest is stored. The manifest tracks file modification times to detect what needs reindexing. |
| `chunk_size` | int | `512` | Target size for token-count splits within a heading section that exceeds `max_chunk_words`. Increase to `600` if passages feel truncated. |
| `chunk_overlap` | int | `64` | Token overlap between adjacent token-count splits. Prevents concepts from being severed at split boundaries. Increase proportionally if you raise `chunk_size`. |
| `max_chunk_words` | int | `400` | Heading sections larger than this are split further by token count. |
| `heading_aware_splitting` | bool | `true` | Split on markdown headings first, then by token count within sections. **Strongly recommended.** Disabling this produces incoherent chunks from multi-section files. |
| `preserve_tables` | bool | `true` | Never split a markdown table mid-row. Tables are kept as a single chunk even if oversized. |
| `preserve_code_blocks` | bool | `true` | Never split a fenced code block. Kept intact even if oversized. |
| `heading_levels` | list[int] | `[2, 3]` | Which heading levels (`#` = 1 through `######` = 6) are used as split boundaries. `[2, 3]` means `##` and `###` headings trigger splits. Adding `1` would split on top-level headings too — useful if your notes use `#` as major section dividers. |
| `batch_size` | int | `32` | Number of chunks sent to the embedding model per batch. Lower if you hit memory errors during indexing. |
| `metadata_fields` | list[string] | *(see above)* | Which metadata fields are stored per chunk in ChromaDB. Do not remove fields — the retriever and ingester depend on all of them. |

**After changing `chunk_size`, `chunk_overlap`, or `heading_levels`:** existing chunks in ChromaDB are stale. Run `ctxkit index` to reindex all affected files.

---

## embeddings

Controls which embedding model generates vectors for your chunks and queries.

```yaml
embeddings:
  backend: ollama
  local_model: nomic-embed-text
  api_model: text-embedding-3-small
  api_key: null
  cache_embeddings: true
  cache_path: ~/.ctxkit/embed_cache
```

| Field | Type | Default | Description |
|---|---|---|---|
| `backend` | string | `"ollama"` | Which embedding provider to use. See options below. |
| `local_model` | string | `"nomic-embed-text"` | Model name for `ollama` or `huggingface` backends. |
| `api_model` | string | `"text-embedding-3-small"` | Model name for `openai` or `gemini` backends. |
| `api_key` | string\|null | `null` | API key for `openai` or `gemini` backends. |
| `cache_embeddings` | bool | `true` | Cache computed embeddings to disk keyed by content hash. Avoids recomputing embeddings for unchanged chunks on reindex. |
| `cache_path` | string | `~/.ctxkit/embed_cache` | Directory for the embedding cache file. |

### Backend options

| `backend` | Requires | Recommended model | Notes |
|---|---|---|---|
| `ollama` | Ollama running | `nomic-embed-text` | Default. Fast on Apple Silicon. 8192 token context. |
| `ollama` | Ollama running | `bge-m3` | Higher quality, 100+ languages. Better for desktop. |
| `huggingface` | Python only | `all-MiniLM-L6-v2` | CPU-only via sentence-transformers. No Ollama needed. Slower than Ollama on Apple Silicon. |
| `huggingface` | Python only | `BAAI/bge-m3` | HuggingFace version of bge-m3. No Ollama needed. |
| `openai` | API key | `text-embedding-3-small` | Fastest option. Near-zero cost at personal vault scale. |
| `gemini` | API key | `models/text-embedding-004` | Google's embedding model. |

> **Important:** If you switch `backend` or `local_model` after indexing, the existing vectors are incompatible. Wipe `~/.ctxkit/chroma_db` and rerun `ctxkit index`.

---

## vector_store

Controls where ChromaDB persists data.

```yaml
vector_store:
  backend: chroma
  persist_path: ~/.ctxkit/chroma_db
  collection_name: ctxkit_vault
  distance_metric: cosine
```

| Field | Type | Default | Description |
|---|---|---|---|
| `backend` | string | `"chroma"` | Only `chroma` is supported. ChromaDB runs as an embedded library — no separate server process needed. |
| `persist_path` | string | `~/.ctxkit/chroma_db` | Directory where ChromaDB writes its data to disk. |
| `collection_name` | string | `"ctxkit_vault"` | ChromaDB collection name. Change this if you want completely separate indexes for different vaults (each needs its own `persist_path` too). |
| `distance_metric` | string | `"cosine"` | Similarity function. `cosine` is correct for semantic search. Only change if you have a specific reason — it affects every similarity score in the system. Options: `cosine`, `l2`, `ip`. |

---

## retriever

Controls how `ctxkit search` finds, assembles, and formats the context package. These are the parameters you tune most often.

```yaml
retriever:
  keyword_prefilter: true
  keyword_prefilter_min_score: 0.3
  keyword_prefilter_mode: hybrid
  top_k: 15
  similarity_threshold: 0.65
  context_block_max_words: 1000
  max_chunks_per_source: 2
  stitch_distance: 2
  stitch_max_words: 400
  signpost_max_items: 8
  signpost_include_section_hints: true
  output_format: markdown
  include_word_count: true
  include_timestamp: true
  include_source_paths: true
  include_similarity_scores: false
```

### Candidate retrieval

| Field | Type | Default | Description |
|---|---|---|---|
| `keyword_prefilter` | bool | `true` | Run a lexical pre-filter before the vector search to narrow candidate files. Eliminates cross-domain false positives (e.g. "health check" pulling in personal health notes). |
| `keyword_prefilter_mode` | str | `hybrid` | How candidates are selected: `hybrid` (BM25 over chunk content + filename/folder matching), `bm25` (BM25 over content only), or `path` (legacy filename/folder term match only). |
| `keyword_prefilter_min_score` | float | `0.3` | Path-match threshold (used in `path` and `hybrid` modes): minimum fraction of query terms that must appear in the filename or folder path. `0.3` = 30%. Raise to `0.5` if unrelated files keep appearing. Does not affect BM25 content matching. |
| `top_k` | int | `15` | Number of chunks returned by the vector search. Higher gives more raw material to assemble from but increases noise risk. Raise to `20` for broad orientation queries. |
| `similarity_threshold` | float | `0.65` | Minimum cosine similarity (0–1) for a chunk to be included. Raise toward `0.75` to cut noise; lower toward `0.55` to cast a wider net. |

### Assembly

| Field | Type | Default | Description |
|---|---|---|---|
| `context_block_max_words` | int | `1000` | Hard cap on the primary context block word count. Raise to `1500` if you consistently hit the limit and want more depth. Note: pasting very large blocks into some LLM interfaces can be unwieldy. |
| `max_chunks_per_source` | int | `2` | Maximum number of chunks included from any single source file. Prevents one large file consuming the entire budget. Raise to `4–6` for comprehensive multi-section files. |
| `stitch_distance` | int | `2` | Maximum chunk index gap to bridge when stitching adjacent chunks into coherent passages. `2` means chunks at positions N and N+2 are stitched (with the intervening chunk included). Raise to `3` if passages feel fragmented. |
| `stitch_max_words` | int | `400` | Maximum words in a single stitched passage. Passages exceeding this are truncated and the remainder moved to the signpost list. |

### Signpost list

| Field | Type | Default | Description |
|---|---|---|---|
| `signpost_max_items` | int | `8` | Maximum number of sources listed in the "Also available" section at the bottom of the context block. |
| `signpost_include_section_hints` | bool | `true` | Include heading breadcrumbs next to each signpost entry so the LLM knows which section of the file is relevant. |

### Output

| Field | Type | Default | Description |
|---|---|---|---|
| `output_format` | string | `"markdown"` | `"markdown"` renders the full cited block with headings and tables. `"plain"` strips markdown formatting. |
| `include_word_count` | bool | `true` | Show word count in the context block header. |
| `include_timestamp` | bool | `true` | Show assembly timestamp in the context block header. |
| `include_source_paths` | bool | `true` | Show full vault-relative file paths in source citations. Disable if paths are long and distracting. |
| `include_similarity_scores` | bool | `false` | Show raw similarity scores per chunk in the output. Debug mode — enables the same output as `--verbose`. |

---

## ingester

Controls how `ctxkit ingest` classifies incoming session summaries and detects conflicts.

```yaml
ingester:
  min_summary_word_count: 100
  min_summary_headings: 1
  similarity_threshold_high: 0.82
  similarity_threshold_low: 0.65
  max_candidates_for_llm: 3
  conflict_detection: true
  conflict_similarity_min: 0.70
  conflict_similarity_max: 0.85
  folder_routing_confidence: 0.75
```

| Field | Type | Default | Description |
|---|---|---|---|
| `min_summary_word_count` | int | `100` | Minimum words required in an incoming summary. Rejects empty pastes and accidental short inputs. |
| `min_summary_headings` | int | `1` | Minimum number of markdown headings required. Ensures the summary has enough structure to classify accurately. |
| `similarity_threshold_high` | float | `0.82` | If the best-matching file scores above this, it's a clear update — no LLM call needed. |
| `similarity_threshold_low` | float | `0.65` | If the best-matching file scores below this, it's a clear new file — no LLM call needed. |
| `max_candidates_for_llm` | int | `3` | When classification is ambiguous (score between the two thresholds), this many top-scoring files are sent to the LLM for adjudication. |
| `conflict_detection` | bool | `true` | Enable sentence-level conflict detection for update cases. Flags pairs where the incoming summary and the existing file make similar-but-different claims on the same topic. |
| `conflict_similarity_min` | float | `0.70` | Lower bound of the conflict band. Sentence pairs below this are unrelated — not flagged. |
| `conflict_similarity_max` | float | `0.85` | Upper bound of the conflict band. Sentence pairs above this are saying the same thing — not flagged. Pairs in band `[0.70, 0.85]` are flagged as potential contradictions. |
| `folder_routing_confidence` | float | `0.75` | For new-file cases: if the folder router's confidence is below this, ctxkit prompts you to confirm the target folder before adding it to the proposal. Above this, the folder is included without prompting. |

---

## writer

Controls how approved ingestion proposals are written to disk.

```yaml
writer:
  require_approval: true
  append_position: end
  frontmatter:
    inject: true
    fields:
      - tags
      - updated
      - related
    tag_max_count: 8
    related_max_count: 5
  backup:
    enabled: true
    backup_path: ~/.ctxkit/backups
    max_backups_per_file: 5
```

| Field | Type | Default | Description |
|---|---|---|---|
| `require_approval` | bool | `true` | **Non-negotiable — always true.** ctxkit never writes without explicit user approval. This field exists for documentation purposes only. |
| `append_position` | string | `"end"` | Where new content is appended in an update. `"end"` appends after the last line. `"after_last_heading"` inserts before the final heading's content, keeping any footer or reference section at the bottom. |

### writer.frontmatter

| Field | Type | Default | Description |
|---|---|---|---|
| `inject` | bool | `true` | Enable frontmatter injection/merging on write. |
| `fields` | list[string] | `["tags", "updated", "related"]` | Which frontmatter fields ctxkit manages. `tags` and `related` are merged (deduplicated) with existing values. `updated` is always set to today's date. |
| `tag_max_count` | int | `8` | Maximum number of tags stored in frontmatter after merge. Oldest tags are dropped when exceeded. |
| `related_max_count` | int | `5` | Maximum number of related file links stored in frontmatter after merge. |

### writer.backup

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Create a timestamped backup before every write. Strongly recommended. |
| `backup_path` | string | `~/.ctxkit/backups` | Directory where backups are stored. Filename format: `<original-name>.<ISO-timestamp>.bak`. |
| `max_backups_per_file` | int | `5` | Rolling limit per file. When exceeded, the oldest backup for that file is deleted. |

---

## llm

Controls the generative model used for classification, proposal generation, and context synthesis.

```yaml
llm:
  backend: ollama
  model: qwen3.5:4b
  synthesise_model: phi4-mini
  api_key: null
  temperature: 0.2
  think: false
  max_tokens: 1000
  timeout_seconds: 30
  fallback_backend: null
  fallback_model: null
  fallback_api_key: null
```

| Field | Type | Default | Description |
|---|---|---|---|
| `backend` | string | `"ollama"` | LLM provider. Options: `ollama`, `openai`, `anthropic`, `gemini`. |
| `model` | string | `"qwen3.5:4b"` | Primary model identifier. Used for ingestion (classification + proposals). For Ollama use the model tag (e.g. `qwen3:8b`). For API backends use the provider's model name (e.g. `gpt-4o-mini`, `claude-haiku-4-5`). |
| `synthesise_model` | string\|null | `null` | Dedicated Ollama model for search synthesis. When set, `ctxkit search` uses this model (non-thinking, fast) to reformat raw excerpts into a coherent briefing. **Recommended: `phi4-mini`** — Microsoft Phi-4 Mini 3.8B is non-thinking, instruction-following, and returns clean output reliably. Leave blank to use `model` for synthesis too. See note below. |
| `api_key` | string\|null | `null` | API key for `openai`, `anthropic`, or `gemini` backends. Not used for `ollama`. |
| `temperature` | float | `0.2` | Generation temperature (0–1). Lower = more deterministic. `0.2` is appropriate for structured classification and proposal tasks. |
| `think` | bool | `false` | Disables thinking mode on Ollama models that support it (e.g. `qwen3.5:4b`, `qwen3:8b`). **Must be `false` for qwen3 models** — thinking mode produces verbose chain-of-thought that breaks structured output parsing. |
| `max_tokens` | int | `1000` | Maximum tokens in the LLM response. Sufficient for classification and short proposals. |
| `timeout_seconds` | int | `30` | Seconds before a slow LLM call is aborted. Increase to `60` on slower hardware or large models. |
| `fallback_backend` | string\|null | `null` | If the primary LLM call fails, retry with this backend. Useful for `ollama → anthropic` failover. Must be one of: `ollama`, `openai`, `anthropic`, `gemini`. |
| `fallback_model` | string\|null | `null` | Model to use on the fallback backend. |
| `fallback_api_key` | string\|null | `null` | API key for the fallback backend. |

> **Why a separate `synthesise_model`?**  
> Thinking models like `qwen3.5:4b` use their `<think>` token budget before producing output. With a `max_tokens: 1000` budget, thinking tokens leave little or nothing for the actual briefing — resulting in empty synthesis output. `phi4-mini` is a non-thinking model: all 1000 tokens go to the briefing itself. It is also significantly faster (~10s vs 3–5min on M2 Air).  
> Run `ollama pull phi4-mini` to use it.

### Recommended models by hardware

| Hardware | `backend` | `model` | `synthesise_model` |
|---|---|---|---|
| Apple M2 Air 16GB | `ollama` | `qwen3.5:4b` | `phi4-mini` |
| Desktop with RTX 4070 | `ollama` | `qwen3:8b` | `phi4-mini` |
| Any hardware (no Ollama) | `openai` | `gpt-4o-mini` | *(leave blank — same model used)* |
| Any hardware (no Ollama) | `anthropic` | `claude-haiku-4-5` | *(leave blank — same model used)* |

---

## manifest

Controls drift detection between the filesystem and the index.

```yaml
manifest:
  path: ~/.ctxkit/manifest.json
  drift_warning_threshold: 3
  drift_warning_age_hours: 24
```

| Field | Type | Default | Description |
|---|---|---|---|
| `path` | string | `~/.ctxkit/manifest.json` | Path to the manifest JSON file. The manifest maps every indexed file to its last-indexed modification time. |
| `drift_warning_threshold` | int | `3` | `ctxkit status` shows a drift warning when this many or more files are out of sync. Lower to `1` if you want immediate alerts on any change. |
| `drift_warning_age_hours` | int | `24` | Reserved for future time-based drift alerts. |

---

## cli

Controls terminal UI behaviour.

```yaml
cli:
  theme: dark
  confirm_before_index: true
  show_similarity_scores: false
  verbose: false
```

| Field | Type | Default | Description |
|---|---|---|---|
| `theme` | string | `"dark"` | Terminal colour scheme. `"dark"` or `"light"`. |
| `confirm_before_index` | bool | `true` | Show the index diff and prompt for confirmation before indexing begins. Set to `false` to skip the prompt (useful in scripts). |
| `show_similarity_scores` | bool | `false` | Show raw cosine similarity scores in search output by default. Same as always passing `--verbose` to `ctxkit search`. |
| `verbose` | bool | `false` | Enable verbose logging output to the terminal across all commands. |

> **Search output location** is not configurable. ctxkit always writes to `<vault.path>/ctxkit-output/`. This folder is automatically excluded from indexing — its contents are never retrieved as vault context.

---

## logging

Controls the rotating log file written to `~/.ctxkit/logs/`.

```yaml
logging:
  enabled: true
  log_path: ~/.ctxkit/logs
  log_level: INFO
  max_log_size_mb: 10
  max_log_files: 5
```

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Enable file logging. Disable to suppress all log output. |
| `log_path` | string | `~/.ctxkit/logs` | Directory for log files. A single rotating log file `ctxkit.log` is written here. |
| `log_level` | string | `"INFO"` | Minimum log level. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Use `DEBUG` when investigating unexpected behaviour — it logs every chunk, every cache hit, and every vector search result. |
| `max_log_size_mb` | int | `10` | Maximum size of the log file before rotation. |
| `max_log_files` | int | `5` | Number of rotated log files to keep. Total disk use is `max_log_size_mb × max_log_files`. |

---

## Full config template

```yaml
vault:
  path: /Users/you/your-vault
  owner_name: ""            # your first name if vault has other-person folders
  excluded_folders: [noise]
  excluded_extensions: [.canvas, .pdf]

indexer:
  min_word_count: 50
  min_structure_signals: 1
  chunk_size: 512
  chunk_overlap: 64
  max_chunk_words: 400
  heading_aware_splitting: true
  preserve_tables: true
  preserve_code_blocks: true
  heading_levels: [2, 3]
  batch_size: 32

embeddings:
  backend: ollama
  local_model: nomic-embed-text

vector_store:
  persist_path: ~/.ctxkit/chroma_db
  collection_name: ctxkit_vault

retriever:
  keyword_prefilter: true
  top_k: 15
  similarity_threshold: 0.65
  context_block_max_words: 1000
  max_chunks_per_source: 2
  stitch_distance: 2

ingester:
  similarity_threshold_high: 0.82
  similarity_threshold_low: 0.65
  conflict_detection: true

writer:
  append_position: end
  frontmatter:
    inject: true
  backup:
    enabled: true
    max_backups_per_file: 5

llm:
  backend: ollama
  model: qwen3.5:4b
  synthesise_model: phi4-mini  # dedicated fast model for ctxkit search synthesis
  think: false
  temperature: 0.2
  timeout_seconds: 30

manifest:
  drift_warning_threshold: 3

cli:
  theme: dark
  confirm_before_index: true

logging:
  log_level: INFO

# Note: search output is always written to <vault.path>/ctxkit-output/ — not configurable.
```

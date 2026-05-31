# mdcore Architecture v2

mdcore is a personal knowledge retrieval system built around a local markdown vault. It indexes notes, retrieves relevant context via semantic search, and can ingest new structured summaries into the vault. LangChain provides the LLM and embedding adapters; all retrieval, ranking, and routing logic is custom.

> **About this version.** This is a standalone, self-contained architecture reference — it supersedes the original `architecture.md` by keeping everything that doc described (components, config, component map, LangChain split) and adding the layer it left out: **how each step is wired to the next**. The original listed steps as a vertical pipeline; this version makes the *edges* explicit — what object one step produces, who consumes it, and what transformation happens in between.
>
> The orchestration code for all flows lives in [`mdcore/cli/commands.py`](../mdcore/cli/commands.py). The steps are pure-ish components — they do **not** call each other directly. The CLI command function is the wiring harness: it instantiates each component, calls them in order, and threads the output of one into the input of the next. **There is no hidden pipeline object** — the data flow *is* the sequence of calls in `commands.py`.

---

## Reading guide: how steps connect

Three connection patterns recur across all flows. Recognise them and the data flow becomes obvious:

1. **Direct handoff** — Step A returns a value, the CLI feeds it straight into Step B. (e.g. `chunks = splitter.split(doc); writer.write(chunks, ...)`)
2. **Branch handoff** — a decision object from one step determines *which* downstream step runs at all. (e.g. `ClassificationDecision.action` decides whether `ConflictDetector` or `FolderRouter` runs — never both.)
3. **Shared dependency, not a handoff** — two steps both receive the same long-lived object (`store`, `engine`, `llm`) at construction time. This is *not* data flowing between steps; it is shared infrastructure. Don't confuse "both touch the vector store" with "A feeds B."

Each step below carries its v1 description **plus** two connection annotations: **Consumes** (what it receives and from whom) and **Produces** (what it returns and who takes it next).

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

The key insight for *connections*: a LangChain `Document` is the universal currency of the indexing flow. Loaders **produce** `Document`; the splitter **consumes** one `Document` and **produces** many `Document` (chunks); the writer **consumes** chunks. The `Document.metadata` dict is the side-channel that carries `source_file`, `chunk_index`, breadcrumbs, etc. across step boundaries without changing the function signatures.

---

## Flow 1: Indexing (`mdcore index`)

Scans the vault, computes what changed, and embeds only the delta. Orchestrated in `commands.py` → `index()`: components are instantiated once (lines ~547-554), then driven in a per-file loop (lines ~622-642).

### Simple overview

The core idea: *don't re-embed the whole vault every time — only touch files that actually changed.* A manifest (a saved list of `file → last-modified-time`) is what makes that possible. No LLM is involved anywhere in indexing.

```
        mdcore index
              │
              ▼
   ┌────────────────────┐
   │ 1. SCAN the vault  │  find all eligible .md/.pdf/.docx files
   │    (VaultScanner)  │  (skip tiny/empty/structureless files)
   └─────────┬──────────┘
             ▼
   ┌────────────────────┐
   │ 2. WHAT CHANGED?   │  compare scan vs the saved manifest
   │  (ManifestManager) │
   └─────────┬──────────┘
             │
   ┌─────────┼──────────────────┬─────────────────────┐
   NEW file              MODIFIED file            DELETED file
 (not in manifest)   (mtime changed)        (in manifest, gone from disk)
   │                       │                         │
   └───────────┬───────────┘                         │
               ▼                                      ▼
   ┌──────────────────────────┐          ┌────────────────────────┐
   │ for each new/modified:   │          │ remove its chunks from │
   │  3. LOAD  → text+metadata│          │ the vector store +     │
   │  4. SPLIT → chunks       │          │ drop from manifest     │
   │  5. EMBED → vectors      │          │ (no loading/embedding) │
   │  6. WRITE → vector store │          └────────────────────────┘
   │  7. mark done in manifest│
   └──────────┬───────────────┘
              ▼
   unchanged files were never touched → next run skips them too
```

**The one idea to take away:** the manifest sits between "what's on disk" and "what gets embedded." Unchanged files never reach the loader. A file that fails midway is *not* marked done, so the next run retries it. Deleted files take a shortcut — they only get removed, never re-processed.

### Component-level data flow

```
VaultScanner.scan()           ──► list[Path]              (eligible files)
        │
        ▼  (whole list, once)
ManifestManager.diff(eligible)──► IndexDiff               (new/modified/deleted)
        │
        ▼  (new + modified paths only, looped one at a time)
   ┌─ per path ────────────────────────────────────────────────┐
   │ DocumentLoader.load(path)  OR  MultiModalLoader.load(path) │
   │        ──► Document                                        │
   │        ▼                                                   │
   │ TextSplitter.split(doc)    ──► list[Document] (chunks)     │
   │        ▼  (chunks + doc.metadata["source_file"])           │
   │ IndexWriter.write(chunks, source_file)                     │
   │        └─ embeds via EmbeddingEngine, upserts to VectorStore│
   │        ▼                                                   │
   │ ManifestManager.update(path)                               │
   └────────────────────────────────────────────────────────────┘
        │
        ▼  (deleted keys, separate loop)
VectorStore.delete(key) + ManifestManager.remove(key)
```

### Step by step

**1. VaultScanner** (`core/indexer/vault_scanner.py`)

Walks the vault directory recursively. For each file:
- Checks extension whitelist (`.md` always; `.pdf`, `.docx`, `.txt` if multimodal enabled)
- Checks minimum word count (default: 50)
- Validates structure signals via `markdown-it` parser (needs headings, paragraphs, or lists)

- **Consumes:** vault config (path, extension whitelist, min word count). No upstream step.
- **Produces:** `list[Path]` of eligible files.
- **Handoff:** the *entire* list is handed to `ManifestManager.diff()` in one call (`diff = manifest.diff(eligible)`). The scanner does not loop or yield per-file; it returns the full set so the manifest can compute set differences.

**2. ManifestManager.diff()** (`core/indexer/manifest_manager.py`)

Loads a JSON file (`manifest.json`) that maps `relative_path → mtime`. Compares against the scanner output to produce an `IndexDiff`:
- `new_files`: in vault, not in manifest
- `modified_files`: in both, but mtime changed
- `deleted_files`: in manifest, no longer in vault

Only the delta is reprocessed — unchanged files are skipped entirely.

- **Consumes:** the `list[Path]` from VaultScanner, plus its own on-disk `manifest.json`.
- **Produces:** an `IndexDiff` dataclass with three lists: `new_files`, `modified_files`, `deleted_files`.
- **Handoff — this is the branch point of the whole flow:** the CLI computes `files_to_index = diff.new_files + diff.modified_files` and loops over *that*, never over the scanner's full output. Unchanged files never reach the loader. `diff.deleted_files` (plain string keys, not `Path` — they no longer exist on disk) take a **separate** path: they skip loading/splitting/embedding entirely and go straight to `VectorStore.delete()`.
- **Why this matters:** the connection between scanner and loader is *not* direct. The manifest sits in the middle and filters. The "delta only" behaviour is entirely an artifact of this edge.

**3. DocumentLoader / MultiModalLoader** (`core/indexer/`)

- **DocumentLoader** (`.md`): uses `python-frontmatter` to separate YAML frontmatter from body. Returns a LangChain `Document` with `page_content` (markdown body) and `metadata` (source path, folder, filename, frontmatter fields).
- **MultiModalLoader** (`.pdf`, `.docx`, `.txt`): extracts plain text, normalises into markdown-like sections. Returns same `Document` structure.

- **Consumes:** one `Path` at a time from the `files_to_index` loop. The CLI picks the loader by extension: `loader.load(path) if path.suffix.lower() == ".md" else mm_loader.load(path)`.
- **Produces:** a single LangChain `Document` (`page_content` + `metadata`, including the critical `source_file` key).
- **Handoff:** the `Document` goes two places. First, `doc` is passed whole to `splitter.split(doc)`. Second, `doc.metadata.get("source_file", str(path))` is read out *separately* and held aside — because the splitter's chunks need a stable source identifier that the writer will use as the ChromaDB delete/upsert key. The loader is the **only** place `source_file` is authoritatively set.

**4. TextSplitter** (`core/indexer/text_splitter.py`)

Custom splitter — does not use LangChain's text splitters.

- **Heading-aware mode** (default on): parses H2/H3 headings, builds a breadcrumb stack (e.g. `Performance > Memory Tuning`), groups text between headings into sections. Tables and code blocks are never split mid-content.
- **Token fallback**: if a section exceeds `chunk_size` words (default: 512), splits on word count with `chunk_overlap` (default: 64) word overlap.

Each output chunk is a `Document` enriched with:
- `heading_breadcrumb`: section hierarchy string
- `chunk_index`, `chunk_total`: position within the file
- `word_count`, `is_table`, `is_code`: content type flags
- `last_indexed`: ISO timestamp

- **Consumes:** the single `Document` from the loader.
- **Produces:** `list[Document]` — one `Document` per chunk, carrying the metadata above.
- **Handoff:** the chunk list is passed to `IndexWriter.write(chunks, source_file)`. Note the *two* arguments: the chunks themselves, and the `source_file` string pulled from the **loader's** metadata (step 3), not from the splitter. The splitter copies `source_file` into each chunk's metadata too, but the writer trusts the explicitly-passed argument for its delete key.

**5. EmbeddingEngine** (`core/indexer/embedding_engine.py`)

Factory-builds a LangChain `Embeddings` instance based on config:

| Config backend | LangChain class | Notes |
|---|---|---|
| `ollama` | `OllamaEmbeddings` | local, free |
| `openai` | `OpenAIEmbeddings` | API key required |
| `gemini` | `GoogleGenerativeAIEmbeddings` | API key required |
| `huggingface` | `HuggingFaceEmbeddings` | local, free |

Caches vectors by SHA256 hash of chunk text. Truncates chunks to 6000 characters before embedding.

- **Consumes:** chunk text — but **not** directly from the splitter. The EmbeddingEngine is a *shared dependency* injected into `IndexWriter` at construction (`writer = IndexWriter(store, engine, cfg.indexer)`). It is called *by* the writer, not by the CLI loop.
- **Produces:** float vectors, cached by SHA256 of chunk text.
- **Connection note:** this is pattern #3 from the reading guide. The engine does not sit "between" the splitter and writer as a pipeline stage; it is a tool the writer reaches for. The same `engine` instance is also handed to the retriever and ingester flows.

**6. IndexWriter → VectorStore** (`core/indexer/index_writer.py`, `store/vector_store.py`)

IndexWriter batches chunks (default: 32 per batch) and calls `VectorStore.upsert()`. VectorStore wraps ChromaDB directly (not via LangChain's Chroma wrapper):
- Single collection named `mdcore_vault`
- Cosine distance metric
- Metadata sanitised to ChromaDB-compatible primitives (str, int, float, bool)
- Deleted files: `store.delete(source_file)` removes all chunks for that file before re-indexing

- **Consumes:** `(chunks, source_file)` from the CLI loop; internally uses the injected `engine` (embeds chunk text) and `store`.
- **Produces:** side effect only — chunks land in ChromaDB. Returns nothing the CLI threads onward.
- **Handoff internals:** for modified files, the writer first calls `store.delete(source_file)` to clear stale chunks, then batches and calls `store.upsert()`. This is why `source_file` had to survive intact from step 3: it is the join key linking "the file on disk" to "its chunks in the vector store."

**7. ManifestManager.update()**

Writes updated `filepath → mtime` pairs. Next run uses this to skip unmodified files.

- **Consumes:** the same `path` the loop is currently on (not the writer's output).
- **Produces:** an in-memory manifest mutation; persisted at loop end.
- **Connection note:** `manifest.update(path)` is called *after* `writer.write()` succeeds, inside the same `try`. If the writer throws, the path is added to `skipped` and the manifest is **not** updated — so a failed file stays "new/modified" on the next run and gets retried. The ordering of these two calls is the entire retry-safety mechanism.

**Deleted-file tail** (lines ~640-642): after the index loop, a separate loop runs `store.delete(key)` then `manifest.remove(key)` for each `diff.deleted_files` entry. These never touch loader/splitter/writer — the only flow-1 path that skips embedding entirely.

---

## Flow 2: Retrieval / Search (`mdcore search <query>`)

Turns a natural language query into a synthesised briefing with source citations. Orchestrated in `commands.py` → `search()` (mirrored in `eval()`). Unlike indexing, this is a **linear transform chain** — each step's return value is the next step's sole input. No branching except the final raw-vs-synthesise toggle.

### Simple overview

The core idea: *find the chunks that answer your query, sew them back into readable passages, fit the best ones into a word budget, then have the LLM write a briefing from them — with citations.* The LLM is called **once, at the very end** (only the synthesise step; `--raw` skips even that).

```
        mdcore search "your question"
                  │
                  ▼
   ┌────────────────────────────┐
   │ 1. NARROW by keywords      │  which files even mention your terms
   │    (KeywordPreFilter)      │  BM25 over content + name/folder → candidate list
   └─────────────┬──────────────┘  (also keeps other people's notes out)
                 ▼
   ┌────────────────────────────┐
   │ 2. SEARCH by meaning       │  embed query, find closest chunks
   │    (VectorSearcher)        │  + rescue good chunks crowded out (see step 2 detail)
   └─────────────┬──────────────┘
                 ▼
   ┌────────────────────────────┐
   │ 3. GROUP + STITCH          │  gather chunks per file, bridge small gaps
   │  (group_by_source, stitch) │  so passages read continuously, not torn
   └─────────────┬──────────────┘
                 ▼
   ┌────────────────────────────┐
   │ 4. RANK + FIT TO BUDGET    │  best sources first; fill ~1000 words;
   │  (rank_sources, assemble)  │  leftovers become "signpost" hints
   └─────────────┬──────────────┘
                 │
        ┌────────┴─────────┐
     --raw flag          default
        │                   │
        ▼                   ▼
 ┌──────────────┐   ┌──────────────────────────┐
 │ write raw    │   │ 5. 🧠 LLM writes briefing│  rewrites excerpts into prose,
 │ excerpts     │   │    from the excerpts     │  cites every claim [1][2]
 │ (no LLM)     │   │    (LLMLayer.synthesise) │
 └──────┬───────┘   └────────────┬─────────────┘
        └────────────┬───────────┘
                     ▼
        saved to <vault>/mdcore-output/<date>-<topic>.md
        (briefing + sources + raw excerpts to verify against)
```

**The one idea to take away:** retrieval narrows then refines — keyword filter cuts scope, semantic search ranks by meaning, stitching makes passages readable, the budget keeps it focused. The LLM only paraphrases what was already retrieved; it never adds facts of its own (and you get the raw excerpts alongside, to check it).

`🧠` = the single LLM call. `--raw` produces the excerpts with no LLM at all.

### Component-level data flow

```
store.all_chunks()             ──► list[Document]  (every chunk: content + metadata)
        │
        ▼
KeywordPreFilter.filter(topic, all_chunks)   # BM25 over content + path match (hybrid)
        ──► set[str] | None    (candidate source files)
        │
        ▼  (candidate_sources + owner-stripped query)
VectorSearcher.search(vector_query, candidate_sources)
        ──► list[Document]     (matched chunks)
        │
        ▼
group_by_source(chunks)        ──► dict[str → list[Document]]
        │
        ▼  (per source)
stitch(source_file, chunks)    ──► list[StitchedPassage]   (per source)
        │   (collected into passages_by_source: dict[str → list[StitchedPassage]])
        ▼
rank_sources(passages_by_source)──► list[(source_file, passages)]  (sorted)
        │
        ▼
assemble(topic, ranked)        ──► AssembledContext  (.primary, .signpost, .source_count)
        │
        ├──► format_context(ctx)          ──► markdown string (raw excerpts)
        └──► raw_text_for_synthesis(ctx)  ──► string ──► LLMLayer.synthesise(topic, raw_text)
                                                              ──► briefing string
```

### Step by step

**0. store.all_chunks()** (the seed)

- **Produces:** `list[Document]` — content + metadata for every chunk in the index.
- **Handoff:** this seeds the prefilter. If it's empty the CLI bails early ("Index is empty"). The prefilter needs the *full* chunk set because BM25 scores over chunk content (and path matching reads filename/folder), so it must see every indexed chunk.

**1. KeywordPreFilter** (`core/retriever/keyword_prefilter.py`)

Lexical pre-filter that runs before vector search, selecting candidate files. Default mode is `hybrid`:
- **BM25** (`rank_bm25.BM25Okapi`) over chunk *content* — files whose body lexically matches the query become candidates, ranked by BM25 and capped to the top 50
- **Path matching** — files whose filename/folder contain query terms (term-presence ratio ≥ `min_score`)
- `hybrid` takes the union of both; `bm25` and `path` modes use one signal only (config: `keyword_prefilter_mode`)
- When the owner name is in the query, files under another person's folder are excluded (cross-person contamination guard)

This reduces vector search scope and prevents cross-person retrieval contamination. It also requires the chunk content, so the CLI now passes `store.all_chunks()` (not just metadata) into the filter.

- **Consumes:** the raw `topic` string + `all_chunks` (content + metadata).
- **Produces:** `set[str]` of candidate `source_file` names — or `None` if prefiltering is disabled or yields nothing (`candidate_sources = prefilter.filter(...) or None`).
- **Handoff:** the set becomes the **second argument** to `VectorSearcher.search(vector_query, candidate_sources)`. It acts as a whitelist: the vector search only returns chunks whose `source_file` is in this set. `None` means "no restriction — search everything."
- **Side wiring — query rewriting between steps:** *between* the prefilter and the searcher, the CLI rewrites the query. It strips the owner's name from `topic` to form `vector_query` (owner name helps keyword routing but is noise for semantic search). So the two steps receive **different** query strings: prefilter gets raw `topic`, searcher gets the stripped `vector_query`. This transformation lives in the CLI, not in either component.

**2. VectorSearcher** (`core/retriever/vector_searcher.py`)

**Phase 1 — broad search, then narrow to candidates:**
- Embeds the query via `engine.embed_query(query)`
- Ranks **the entire vault** and takes the global top `top_k * 2` chunks (`store.search`, no candidate filter applied yet)
- *Then* throws away any chunk not from a keyword-matched candidate file, and trims to `top_k`
- Drops anything below `similarity_threshold` (default: 0.65, cosine)

**Phase 2 — keyword rescue:** for each candidate file that ended up with **zero** chunks after phase 1, re-search *inside that one file only* (`store.search_in_sources`, ChromaDB `$in` filter) and keep chunks at a relaxed bar of `similarity_threshold * 0.75` (≈0.49).

Why this is needed — and it's **not** mainly the lower threshold:

> Phase 1 ranks every chunk in the vault and keeps only the global top 30 (when `top_k=15`). In a large vault those 30 slots get eaten by chunks from other files. A file whose **name** matched your query can have a perfectly good chunk (say 0.72, well above 0.65) yet still never make the top 30 — it gets **crowded out** and vanishes, not because it's irrelevant but because other files outranked it.
>
> Phase 2 fixes exactly this: searching one file in isolation removes the competition, so its good chunks surface. The relaxed 0.49 bar is the *secondary* effect — it additionally lets weak-but-relevant chunks (e.g. 0.55) scrape through.

In short: **rescue un-buries keyword-relevant files that the global top-`2k` search crowded out** (the main win), and loosens the threshold for genuine vocabulary mismatch (minor). The code's own example: query `"emigration"` matches a folder named `Emigration/`, but the body talks about `visa`/`sponsorship` — the file is clearly on-topic by name yet scores low semantically.

- **Consumes:** `(vector_query, candidate_sources)`. Uses the injected `engine` to embed the query and the injected `store` to search.
- **Produces:** `list[Document]` — the matched chunks (flat, unordered across sources).
- **Handoff:** the flat chunk list goes straight to `group_by_source(chunks)`. If empty, the CLI bails ("No results found"). The chunks carry their `source_file` and `chunk_index` in metadata — those two fields are what every downstream step keys on.

**3. ChunkGrouper** (`core/retriever/chunk_grouper.py`) — module function `group_by_source`

Groups chunks by `source_file`, sorts each group by `chunk_index`. Output: `dict[source_file → list[chunks]]`.

- **Consumes:** the flat `list[Document]`.
- **Produces:** `dict[source_file → list[Document]]`, each list sorted by `chunk_index`.
- **Handoff:** the dict is consumed by a **dict comprehension** that calls `stitch` once per source: `passages_by_source = {sf: stitch(sf, c, cfg.retriever) for sf, c in groups.items()}`. This is the point where the flow fans out per-source and the stitcher is invoked repeatedly.

**4. ChunkStitcher** (`core/retriever/chunk_stitcher.py`) — module function `stitch`

Bridges gaps between non-consecutive retrieved chunks within a single source:
- If two retrieved chunks are within `stitch_distance` (default: 2) indices of each other, fills in the chunks between them
- Caps per-source chunks at `max_chunks_per_source + 2` to avoid context bloat
- Truncates stitched passages at `stitch_max_words` (default: 400)

Output per source: a `StitchedPassage` with joined text, merged breadcrumbs, and average similarity.

- **Consumes:** one `(source_file, list[Document])` pair at a time (from the comprehension above).
- **Produces:** `list[StitchedPassage]` for that one source.
- **Handoff:** results are reassembled by the comprehension into `passages_by_source: dict[str → list[StitchedPassage]]`, handed whole to `rank_sources`.

**5. SourceRanker** (`core/retriever/source_ranker.py`) — module function `rank_sources`

Sorts sources by mean similarity score of their passages, descending. Higher-similarity sources appear first in context.

- **Consumes:** the `passages_by_source` dict.
- **Produces:** `list[(source_file, passages)]` — the same data, now an **ordered list** instead of an unordered dict. The transformation is purely *ordering* — no data added or dropped.
- **Handoff:** the ranked list is passed positionally to `assemble(topic, ranked, cfg.retriever)`. Order matters from here on: the assembler walks the list front-to-back and spends its word budget on whatever comes first.

**6. ContextAssembler** (`core/retriever/context_assembler.py`) — module function `assemble`

Enforces the word budget (`context_block_max_words`, default: 1000):
- Iterates ranked sources, includes passages until budget is exhausted
- Truncates the final passage if needed to fit exactly
- Sources that do not fit become "signpost" entries: breadcrumb hints + suggested follow-up queries only

- **Consumes:** `(topic, ranked, cfg)`.
- **Produces:** an `AssembledContext` dataclass with `.primary` (`list[(source_file, list[StitchedPassage])]` that fit the budget), `.signpost` (`list[(source_file, breadcrumbs)]` for sources that didn't fit), and `.source_count` (= `len(primary)`).
- **Handoff — the fan-out into output:** `ctx` is the last shared object. It feeds **two or three** consumers depending on the `--raw` flag:
  - `format_context(ctx, cfg)` → markdown of the raw excerpts (always called).
  - `raw_text_for_synthesis(ctx)` → a plain numbered-blocks string (only in synthesise mode) → fed to `LLMLayer.synthesise(topic, raw_text)`.
  - `ctx.primary` is also iterated directly in the CLI to build the `## Sources` citation list (`for i, (sf, _) in enumerate(ctx.primary, 1)`).

**7. ContextFormatter** (`core/retriever/context_formatter.py`) — `format_context` / `raw_text_for_synthesis`

Renders assembled context as markdown:
- Numbered citations `[1]`, `[2]` per source
- Breadcrumb trail per source
- Signpost table if any sources were excluded from primary context
- Extracts raw text for synthesis prompt (numbered blocks format, capped at 4000 chars)

- **Consumes:** the `AssembledContext`.
- **Produces:** strings. `format_context` → human-facing markdown. `raw_text_for_synthesis` → the LLM-facing prompt body.
- **Connection note:** these two functions are the *adapter* between the retrieval data model (`AssembledContext`) and the two different string formats their consumers need (a human reading a file vs. an LLM reading a prompt). Same input, two purpose-built outputs.

**8. LLMLayer.synthesise()** (`llm/llm_layer.py`)

Constructs a synthesis prompt instructing the LLM to:
- Rewrite excerpts into a coherent briefing
- Cite every claim with `[source_number]`
- Use only information present in the excerpts

Determines which LLM to use:
1. `synthesise_backend` + `synthesise_model` if explicitly configured
2. Primary backend + `synthesise_model` (different model, same provider)
3. Primary backend + primary model (full fallback)

- **Consumes:** `(topic, raw_text)` — the topic for framing, the formatted excerpts as grounding.
- **Produces:** a `briefing` string.
- **Handoff:** the CLI interpolates `briefing` plus the `format_context` output plus the source list into one markdown file written to `<vault>/mdcore-output/`. This is the terminal sink of flow 2 — nothing consumes the file programmatically (it's excluded from indexing).

---

## Flow 3: Ingestion (`mdcore ingest <file>`)

Takes a structured summary (markdown with headings) and decides whether to update an existing vault note or create a new one, then writes it. Orchestrated in `commands.py` → `ingest()` then `_execute_write()`. This flow has the **most branching**: the `ClassificationDecision.action` field ("update" vs "new") routes the data down two mutually exclusive sub-paths before they reconverge at the proposal step.

### Simple overview (every branch + LLM touchpoints)

The core question this flow answers: *is this summary an update to a note I already have, or a brand-new note — and if new, which folder?* That one decision (step 3) drives everything after it. The diagram below shows all branches and marks where an LLM is actually called (`🧠`) versus where the flow waits for you (`👤`).

```
        You hand mdcore a summary (markdown + headings)
                            │
                            ▼
              ┌──────────────────────────┐
              │ 1. VALIDATE              │  enough words? has a heading?
              │    (SummaryReceiver)     │
              └────────┬─────────────────┘
                  fail │ → reject, stop
                       ▼ pass
              ┌──────────────────────────┐
              │ 2. EMBED                 │  whole-summary vector
              │    (SummaryEmbedder)     │  + per-sentence vectors (saved for 4a)
              └────────┬─────────────────┘
                       ▼
              ┌──────────────────────────┐
              │ 3. CLASSIFY: update/new? │  compare summary vs every note → best score
              │   (ClassificationEngine) │
              └────────┬─────────────────┘
                       │
   ┌───────────────┬───┴───────────┬────────────────────────┐
   │               │               │                        │
 ≥0.82      0.65–0.82 (AMBIG)    ≤0.65            2+ headings AND table/list
   │               │               │              (self-contained → forced)
   │               ▼               │                        │
   │      ┌─────────────────┐      │                        │
   │      │ 🧠 ask the LLM  │      │                        │
   │      │ "update or new?"│      │                        │
   │      └───┬────────┬────┘      │                        │
   │    update│        │new        │                        │
   ▼          ▼        ▼           ▼                        ▼
 ┌──────────────────────┐    ┌──────────────────────────────────────┐
 │  4a. UPDATE path     │    │  4b. NEW path                        │
 │  read existing note, │    │  gather candidate folders:           │
 │  flag clashing       │    │   • folders of similar notes         │
 │  sentences           │    │   • your .mdcore-meta.yaml descriptions│
 │  (ConflictDetector)  │    │  🧠 LLM picks best folder (FolderRouter)│
 │                      │    │        │                             │
 │                      │    │   confident? ── no → 👤 ASK YOU to    │
 │                      │    │        │ yes        confirm / override │
 └──────────┬───────────┘    └────────┴─────────────────┬───────────┘
            │                                            │
            └─────────────────────┬──────────────────────┘
                                  ▼
                    ┌──────────────────────────┐
                    │ 5. DRAFT THE TEXT        │
                    │ 🧠 LLM writes exact edit │  update → insert/replace
                    │    or full new document  │  new    → whole doc
                    │   (ProposalGenerator)    │
                    └────────┬─────────────────┘
                             ▼
                    ┌──────────────────────────┐
                    │ 6. PREVIEW + 👤 ASK YOU  │  shows action, target/folder,
                    │   Approve / Edit / Reject│  confidence, flagged conflicts
                    └────────┬─────────────────┘
                  reject ────┤  nothing written, stop
                  edit   ────┤  fix summary, re-run mdcore ingest
                  approve     ▼
                    ┌──────────────────────────┐
                    │ 7. WRITE + INDEX         │  update: backup old file first
                    │  backup → frontmatter →  │  inject tags/date/related
                    │  write → reindex 1 file  │  reindex THAT file (→ Flow 1)
                    │  (Writer + IndexTrigger) │
                    └────────┬─────────────────┘
                             ▼
              note is in your vault + instantly searchable
```

**The four classification routes (step 3):**
- **score ≥ 0.82** → straight to UPDATE, no LLM.
- **0.65 < score < 0.82** → ambiguous: 🧠 ask the LLM, which answers update *or* new, then routes accordingly.
- **score ≤ 0.65** → straight to NEW, no LLM.
- **2+ H2 headings AND a table/list** → forced NEW (self-contained document), skips the score check entirely.

**LLM call count (`🧠`):** the classification *fork* only uses the LLM in the ambiguous middle band, but folder-picking (4b) and drafting (5) are LLM calls regardless of score. So:
- ambiguous → new: **3 calls** (classify, folder, draft)
- clear-cut new: **2 calls** (folder, draft)
- clear-cut update: **1 call** (draft)

**Where it waits for you (`👤`):** step 6 always; step 4b only when folder confidence is low. mdcore never writes to the vault without showing you the proposal first — the whole flow is propose-then-confirm.

### Component-level data flow

```
SummaryReceiver.receive_*()    ──► summary (validated str)
        │
        ▼
SummaryEmbedder.embed(summary) ──► SummaryEmbeddings(.full, .sentences, .sentence_embeddings)
        │
        ▼  (embs.full + summary)
ClassificationEngine.classify(embs.full, summary)
        ──► ClassificationDecision(.action, .target_file, .confidence, .top_scores, .used_llm)
        │
        ├── action == "update" ──► read existing file ──► ConflictDetector.detect(existing, summary)
        │                                                       ──► list[Conflict]
        │
        └── action == "new" ─────► FolderRouter.route(summary, top_scores=decision.top_scores)
                                        ──► (folder, confidence)
        │
        ▼  (decision + summary + existing_content + conflicts + folder + fm_updates)
ProposalGenerator.generate(...)──► Proposal(.action, .target_file, .suggested_folder,
        │                                    .proposal_text, .conflicts, .frontmatter_updates)
        ▼
   [user: Approve / Edit / Reject]
        │
        ▼  (_execute_write — branches again on proposal.action)
BackupManager → FrontmatterInjector → FileWriter → IndexTrigger.reindex()
                                                        └─ runs Flow 1 on the one new/changed file
```

### Step by step

**1. SummaryReceiver** (`core/ingester/summary_receiver.py`)

Validates the incoming summary: minimum word count (default: 100), minimum heading count (default: 1). Rejects bare text dumps.

- **Consumes:** a file path (`--file`) or stdin text. The CLI chooses: `receive_from_file(file)` vs `receive_from_text(lines)`.
- **Produces:** a validated `summary` string (raises `ValueError` if too short / too few headings — caught by the CLI which exits 1).
- **Handoff:** the same `summary` string is reused by **every** later step (embedder, classifier, conflict detector, folder router, proposal generator, and the final writer). It is the persistent payload of the whole flow — held in a local variable and passed positionally each time. Nothing mutates it.

**2. SummaryEmbedder** (`core/ingester/summary_embedder.py`)

- Embeds the full summary as a single vector (for file-level classification)
- Splits summary into sentences (split on `.!?`, minimum 5 words each)
- Embeds each sentence individually (for conflict detection later)

- **Consumes:** the `summary` string. Uses the shared `engine`.
- **Produces:** a `SummaryEmbeddings` dataclass with `.full` (one vector for the whole summary), `.sentences` (the split sentence strings), and `.sentence_embeddings` (per-sentence vectors).
- **Handoff — split by field:** the fields go to **different** downstream steps. `embs.full` → `ClassificationEngine.classify()` (file-level match). The per-sentence embeddings are computed here but consumed *later and conditionally* by `ConflictDetector` (only on the "update" branch). The embedder front-loads all embedding work; downstream steps pick the field they need.

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

- **Consumes:** `(embs.full, summary)`. Uses shared `store` (mean embedding per indexed file) and `llm` (ambiguous-case disambiguation).
- **Produces:** a `ClassificationDecision` dataclass: `action` ("update"|"new"), `target_file` (str|None), `confidence` (float), `reasoning` (str), `used_llm` (bool), `top_scores` (`dict[file → similarity]`, top 10).
- **Handoff — this is the master branch switch of flow 3.** Several fields fan out:
  - `.action` decides which of the next two steps runs — `ConflictDetector` (if "update") **or** `FolderRouter` (if "new"). Never both.
  - `.target_file` (update only) tells the CLI which existing file to read off disk for conflict detection and, later, which file to overwrite.
  - `.top_scores` is passed into `FolderRouter.route(summary, top_scores=decision.top_scores)` so the router reuses the similarity scores already computed here instead of re-querying the store.
  - the whole `decision` object is also passed forward to `ProposalGenerator.generate(decision, ...)`.

**4. FolderRouter** (`core/ingester/folder_router.py`) — new files only

Decides which vault folder the new file should live in.

- **Semantic candidates**: extracts parent folders from top-k semantically similar files (if any exist above 0.60 similarity)
- **VaultMap overlay**: merges any folder descriptions from `.mdcore-meta.yaml` (user-authored descriptions)
- **LLM routing**: passes candidate folders with descriptions to the LLM, which picks the most appropriate one and is constrained to the provided list

Output: `(folder_path, confidence)`.

- **Consumes:** `(summary, top_scores=decision.top_scores)`. Also reads `.mdcore-meta.yaml` folder descriptions (the output of Flow 4). Uses shared `llm`.
- **Produces:** `(folder_path, confidence)`.
- **Handoff:** `folder` flows into `ProposalGenerator.generate(...)`. The CLI inserts a **human checkpoint** between this step and the proposal: if `router.needs_confirmation(confidence)` is true, it prompts the user to accept or override the folder before continuing. So the edge from router → proposal can be intercepted by the user.

**5. ConflictDetector** (`core/ingester/conflict_detector.py`) — update files only

Detects near-duplicate sentences between the existing file and incoming summary:
- Computes pairwise cosine similarities across all sentence embeddings
- Flags pairs where similarity falls between `conflict_similarity_min` (0.88) and `conflict_similarity_max` (0.97) — this range targets restatements, not exact duplicates
- Returns top 10 conflict pairs

- **Consumes:** `(existing_content, summary)`. The CLI reads `existing_content` off disk first using `decision.target_file` (`vault_path / decision.target_file`). Re-embeds sentences internally via the shared `engine`.
- **Produces:** `list[Conflict]` (top 10 near-duplicate sentence pairs).
- **Handoff:** `conflicts` is passed into `ProposalGenerator.generate(...)` so the LLM (and the user-facing proposal panel) can flag restatements. On the "new" branch `conflicts` stays `[]`.

**6. ProposalGenerator → LLMLayer.propose()** (`core/ingester/proposal_generator.py`)

Calls `LLMLayer.propose()` with the classification result, existing file content (if update), and incoming summary. The LLM generates the exact text to write: insertion/replacement for "update", or a complete document for "new".

- **Consumes — the convergence point:** `(decision, summary, existing_content, conflicts, folder, fm_updates)`. This single call gathers the outputs of **both** branches (`existing_content`+`conflicts` from update; `folder` from new) plus the classification `decision` and a freshly-built `fm_updates` dict (`{updated, tags, related}`). Whichever branch didn't run contributes its empty default (`""` / `[]`).
- **Produces:** a `Proposal` dataclass: `action`, `target_file`, `suggested_folder`, `proposal_text` (the LLM-generated content to write), `conflicts`, `frontmatter_updates`, `confidence`.
- **Handoff:** `proposal` goes to `_render_proposal()` (display) then gates on a user `Approve / Edit / Reject` prompt. Only "Approve" passes `proposal` into `_execute_write()`.

**7. Writer** (post-approval) — `_execute_write()`, branches again on `proposal.action`

After user approves the proposal:
- **BackupManager**: timestamped copy of the existing file
- **FrontmatterInjector**: updates YAML frontmatter (tags, `updated` date, `related` links)
- **FileWriter**: appends or inserts content at configured position
- **IndexTrigger**: re-indexes only the modified file

- **Consumes:** `(cfg, proposal, summary, existing_content)`.
- Update path: `BackupManager.backup(target)` → `FrontmatterInjector.inject(target, proposal.frontmatter_updates)` returns `updated_fm` → `FileWriter.update(target, updated_fm, summary)` → `IndexTrigger.reindex(target)`. The injector's return value (`updated_fm`) is fed straight into the file writer; that's the one internal handoff here.
- New path: `FileWriter.create(proposal.suggested_folder, filename, summary)` returns `new_path` → `IndexTrigger.reindex(new_path)`. The `filename` is derived from the summary's first heading via `_derive_filename(summary)`.
- **Handoff — flow 3 feeds flow 1:** `IndexTrigger.reindex(path)` re-runs the **indexing pipeline** (loader → splitter → index_writer → manifest) on the single written file, via the `_factory()` closure that builds those four components. This closes the loop: an ingested note is immediately searchable. The trigger is the explicit edge from Flow 3 back into Flow 1.

---

## Flow 4: Vault Map (`mdcore map`)

Generates or updates `.mdcore-meta.yaml` — a human-editable YAML file describing what each vault folder is for. Used by FolderRouter during ingestion to improve routing accuracy. Orchestrated in `commands.py` → `vault_map_cmd()`. This flow is mostly a **producer for Flow 3** — its output is consumed by `FolderRouter` later, not within this flow.

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

### Connections

- `all_vault_folders()` **produces** the folder list; `write_template()` **consumes** it (plus any existing `.mdcore-meta.yaml` to preserve hand-written descriptions) and writes the YAML.
- `--repair` mode uses `stale_descriptions()` → `remove_description()` → `save()`: a self-contained read-modify-write, no cross-flow edge.
- **The cross-flow connection (the whole point of this flow):** there is **no in-process handoff** to another flow. The connection is *through the filesystem and across invocations*. `mdcore map` writes `.mdcore-meta.yaml`; the user edits it; on the next `mdcore ingest`, `FolderRouter.route()` reads it. This is a **deferred, file-mediated edge** — the only one in mdcore where two flows connect via a file a human edits in between, rather than a Python value passed in memory. It improves routing for novel document types with no close semantic matches in the vault.

---

## Flow 5: Status (`mdcore status`)

Health check showing index freshness, drift, chunk count, and active LLM key. Orchestrated in `commands.py` → `status()`. A **read-only diagnostic** — it reuses Flow 1's first two steps but feeds them into a report instead of into loaders.

```
VaultScanner.scan()            ──► list[Path]
        │
        ▼
ManifestManager.diff(eligible) ──► IndexDiff
        │
store.all_metadata()           ──► list[dict]  (parallel, independent fetch)
        │
        ▼  (diff + metadata → derived counts)
Rich table  +  drift warning (if diff.total_changes >= threshold)
        │
        ▼  (aggregator backend only)
AggregatorChat.current_key()   ──► active-key table
```

**What it reports:**
- Vault path and eligible file count
- Indexed file count and total chunk count
- Drift: new files not yet indexed, modified files with stale embeddings, deleted files still in index
- Warning if total drift >= `drift_warning_threshold` (default: 3)
- If `backend: aggregator`: active llm-keypool provider, model, request count, cooldown status

### Connections

- **Shared front-half with Flow 1:** `status()` calls the exact same `VaultScanner.scan()` → `ManifestManager.diff(eligible)` pair as `index()`. The difference is purely in the *consumer*: `index()` feeds `diff` into the file loop; `status()` feeds `diff` into a Rich table and a threshold check (`diff.total_changes >= cfg.manifest.drift_warning_threshold`). Same producers, different sink. This is why drift numbers in `status` always match what `index` would act on — they share the producing code path.
- **`store.all_metadata()`** is fetched independently (not derived from the diff). The CLI derives two scalars from it: `indexed_files = len({m["source_file"] for m in all_meta})` and `total_chunks = len(all_meta)`. The connection is a local aggregation, not a component call.
- **Aggregator tail:** only if `cfg.llm.backend == "aggregator"`, `AggregatorChat.current_key()` is consulted for a separate key-pool table. Independent of the index data above — no edge between them.

---

## Cross-flow connection map

The connections *between* flows:

| Producer | Artifact | Consumer | Mechanism |
|---|---|---|---|
| Flow 1 (index) | ChromaDB chunks + manifest | Flow 2 (search), Flow 3 (classify) | shared `store`, in-process |
| Flow 3 (ingest) | written `.md` file | Flow 1 (index) | `IndexTrigger.reindex()` — direct call |
| Flow 4 (map) | `.mdcore-meta.yaml` | Flow 3 (`FolderRouter`) | filesystem, deferred across invocations + human edit |
| Flow 1 (scan+diff) | `IndexDiff` | Flow 5 (status) | shared component code path |
| (init) | `config.yaml` | all flows | loaded once per command via `_load()` |

**Shared infrastructure (not flow edges):** `store`, `engine`, and `llm` are each constructed per-command (`_make_store`, `_make_engine`, `LLMLayer(cfg.llm)`) and injected into whichever components need them. Multiple steps touching the same `store` is shared state, not data flowing between those steps — keep this distinction in mind when reading any flow above.

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
│   └── commands.py                  CLI entrypoint + wiring harness for all flows
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
│   ├── writer/
│   │   ├── backup_manager.py        Timestamped backup of target file
│   │   ├── frontmatter_injector.py  YAML frontmatter updates
│   │   ├── file_writer.py           Append / insert / create
│   │   └── index_trigger.py         Re-index single written file (→ Flow 1)
│   └── vault_map.py                 .mdcore-meta.yaml read/write
├── store/
│   └── vector_store.py              ChromaDB wrapper
├── llm/
│   └── llm_layer.py                 LLM backend factory + classify/propose/synthesise
├── serve/
│   └── chain.py                     LangChain Runnable pipeline (for LangSmith eval)
└── mcp_server/
    └── server.py                    MCP stdio server (search_vault, index_vault tools)
```

---

## What this version makes explicit

Quick reference for the connections that a step-list alone glosses over:

1. **Scanner → Manifest is not direct** — the manifest *filters* the scanner output into a delta; only the delta reaches the loader.
2. **`source_file` is set once (loader) and threaded everywhere** — it is the join key between disk files and vector-store chunks; the writer trusts the explicitly-passed argument.
3. **Query is rewritten between prefilter and searcher** — owner name stripped; the two steps see different strings.
4. **Retrieval is a pure linear transform** — dict → per-source stitch → ordered list → budgeted context → two string formats. Order is introduced at `rank_sources` and consumed by `assemble`.
5. **`ClassificationDecision.action` is the master branch** — it selects ConflictDetector XOR FolderRouter; both branches reconverge only at `ProposalGenerator.generate()`.
6. **`SummaryEmbedder` front-loads work, fields fan out** — `.full` to classifier, sentence embeddings to the (conditional) conflict detector.
7. **Ingest closes back into index** — `IndexTrigger.reindex()` is the explicit Flow 3 → Flow 1 edge.
8. **Flow 4 connects through a file a human edits** — the only deferred, file-mediated cross-flow edge.
9. **`engine`/`store`/`llm` are shared tools, not pipeline stages** — don't read "both use the store" as "A feeds B."

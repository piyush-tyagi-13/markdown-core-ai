## mdcore/core/indexer/ — Indexer Module

### Purpose
Implements the full indexing pipeline: scanning vault files, loading documents, splitting into chunks, embedding, and writing to ChromaDB. Also manages the manifest.json index state and embed_cache.pkl.

### Public interface

**`VaultScanner(vault_cfg: VaultConfig, indexer_cfg: IndexerConfig)`**
- `scan() -> list[Path]` — returns absolute paths of eligible vault files
- Eligible: `.md` always; `.pdf`/`.docx`/`.txt` only if `vault_cfg.index_pdf`/`.index_docx`/`.index_txt` = True
- Excluded: `.mdcore-meta.yaml` (by name), anything in excluded_folders (case-insensitive, ANY path part), anything with excluded_extension suffix, word_count < min_word_count (50), structure signals < min_structure_signals (1) [except .pdf/.docx/.txt which bypass structure check]
- `mdcore-output` is ALWAYS excluded regardless of user config (hardcoded addition to excluded_folders set)
- Structure signals: count of `heading_open`, `paragraph_open`, `bullet_list_open` tokens from markdown-it-py parse
- Does NOT catch OS exceptions — if a file can't be read, it propagates (UNCLEAR: rglob may yield files that become unreadable mid-scan)

**`ManifestManager(manifest_cfg: ManifestConfig, vault_cfg: VaultConfig)`**
- Data: `dict[str, float]` — vault-relative path -> float mtime
- `diff(eligible_files: list[Path]) -> IndexDiff` — compares manifest vs filesystem
  - new_files: path in eligible but key not in manifest
  - modified_files: path in eligible AND manifest_mtime < file.stat().st_mtime
  - deleted_files: key in manifest but not in eligible_keys
- `update(path: Path) -> None` — sets manifest[key] = path.stat().st_mtime, saves JSON immediately
- `remove(source_key: str) -> None` — pops key, saves JSON immediately
- `_key(path: Path) -> str` — `str(path.relative_to(vault_path))` or `str(path)` if ValueError
- Saves after every update/remove (not batched). For large vaults this means N JSON writes for N files.
- Corrupt/missing manifest loads as empty dict (no exception raised to caller)

**`DocumentLoader(vault_cfg: VaultConfig)`**
- `load(path: Path) -> Document` — loads .md file
- Parses YAML frontmatter via `python-frontmatter`. On parse failure: content=raw text, fm={}
- Metadata: source_file (vault-relative), folder_path (parent dirs), filename, frontmatter (dict)
- Does NOT strip frontmatter from content — `python-frontmatter` returns `post.content` which is content WITHOUT frontmatter block

**`MultiModalLoader(vault_cfg: VaultConfig)`**
- `load(path: Path) -> Document` — loads .pdf, .docx, or .txt
- PDF: `pypdf.PdfReader` — extracts text per page, prefixes each with `## Page N`. Empty text for scanned PDFs logged as warning, returns Document with empty page_content.
- DOCX: `python-docx` — converts Heading1/2/3 styles to #/##/###, extracts tables as markdown pipe format
- TXT: plain `path.read_text()`
- Raises ImportError with install hint if pypdf/docx missing
- Raises ValueError for unsupported suffix
- Metadata: same as DocumentLoader + `file_type: str` (pdf/docx/txt)

**`TextSplitter(cfg: IndexerConfig)`**
- `split(doc: Document) -> list[Document]`
- IF `heading_aware_splitting=True`:
  - Pattern: `^(#{N,M})\s+(.+)$` where N=min(heading_levels), M=max(heading_levels). Default: `^(#{2,3})\s+(.+)$`
  - Heading stack tracks H2/H3 nesting for breadcrumb: "H2 Title > H3 Title"
  - Text between headings becomes a section
  - If section wc < min_word_count (50) AND prior chunks exist: append to previous chunk
  - If section wc > max_chunk_words (400): call _split_by_tokens()
    - IF preserve_tables AND table in text: return whole text (no split)
    - IF preserve_code_blocks AND code block in text: return whole text (no split)
    - ELSE: word-based split, chunk_size words (512), chunk_overlap words (64) overlap
- Metadata added per chunk: heading_breadcrumb, chunk_index, chunk_total, word_count, is_table, is_code, last_indexed (UTC ISO timestamp)
- GOTCHA: `chunk_size` (512) is compared against word count, NOT token count (comment in code: "treat chunk_size as word count for simplicity")

**`EmbeddingEngine(cfg: EmbeddingsConfig)`**
- `embed_texts(texts: list[str]) -> list[list[float]]` — batch embed, with cache
- `embed_query(text: str) -> list[float]` — single embed, NO cache lookup
- Cache: `dict[sha256(text), embedding]` in embed_cache.pkl
- Truncation: texts > 6000 chars are truncated before embedding (logged as warning). Full text still stored in ChromaDB.
- Does NOT cache embed_query results — only embed_texts
- On cache load failure (corrupt pkl): silently starts with empty cache

**`IndexWriter(store: VectorStore, engine: EmbeddingEngine, cfg: IndexerConfig)`**
- `write(chunks: list[Document], source_file: str) -> None`
- First: `store.delete(source_file)` — removes all existing chunks for this file
- Sanitizes metadata: strips any non-primitive values (dicts, lists not allowed in ChromaDB)
- Embeds in batches of `cfg.batch_size` (32)
- Upserts all chunks to ChromaDB

### Execution paths

**Delta index:**
VaultScanner.scan() -> ManifestManager.diff() -> [for each file] DocumentLoader/MultiModalLoader.load() -> TextSplitter.split() -> IndexWriter.write() -> ManifestManager.update()

**Force index:** Delete manifest.json + chroma_db/ + embed_cache.pkl -> then delta index (empty state = everything is "new")

**Single-file reindex (after ingest write):** DocumentLoader.load() -> TextSplitter.split() -> IndexWriter.write() -> ManifestManager.update()

### Data files
- `manifest.json` — JSON dict, path: cfg.manifest.path (resolved relative to vault root if not absolute)
- `embed_cache.pkl` — pickle dict, path: cfg.embeddings.cache_path / "embed_cache.pkl"
- `chroma_db/` — ChromaDB persistent storage, path: cfg.vector_store.persist_path

### Side effects
- Reads all vault files (VaultScanner, DocumentLoader, MultiModalLoader)
- Reads/writes manifest.json (ManifestManager)
- Reads/writes embed_cache.pkl (EmbeddingEngine)
- Reads/writes ChromaDB (IndexWriter via VectorStore)
- EmbeddingEngine makes LLM/API calls for new embeddings

### Gotchas
- ManifestManager saves JSON after EVERY file update — N files = N disk writes. For large vaults this is slow.
- embed_query() does NOT use cache — repeated identical queries re-embed each time.
- TextSplitter appends under-length sections to the PREVIOUS chunk (not next) — last section may grow large.
- VaultScanner reads entire file text to count words — for large files this is a full read before deciding to skip.
- IndexerConfig.manifest_path field (default "~/.mdcore/manifest.json") is NEVER READ. Dead field. Manifest path comes from ManifestConfig.path.
- chunk_size=512 is word count, not token count. Documentation/field name implies tokens, code treats as words.

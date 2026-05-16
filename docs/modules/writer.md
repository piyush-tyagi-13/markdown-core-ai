## mdcore/core/writer/ — Writer Module

### Purpose
Implements the write pipeline for Flow B: backing up the target file, updating YAML frontmatter, writing file content atomically, and triggering a single-file reindex of the written file.

### Public interface

**`BackupManager(cfg: BackupConfig)`**
- `backup(path: Path) -> Path | None`
- If `cfg.enabled=False` or file doesn't exist: returns None (no backup)
- Copies file to `cfg.backup_path / "{filename}.{ISO-timestamp}.bak"`
- Timestamp format: `%Y-%m-%dT%H-%M-%S` (dashes for cross-platform filename safety)
- Uses `shutil.copy2` (preserves metadata)
- Rotates: deletes oldest .bak files if count > cfg.max_backups_per_file (5)
- Backup path: `~/.mdcore/backups/` by default

**`FrontmatterInjector(cfg: FrontmatterConfig)`**
- `inject(path: Path, updates: dict) -> str` — returns updated file content string (does NOT write)
- Reads current file via `python-frontmatter`
- Merges fields listed in `cfg.fields` (default: tags, updated, related):
  - tags: deduplicates existing + new, caps at tag_max_count (8)
  - related: deduplicates existing + new, caps at related_max_count (5)
  - updated: sets to updates["updated"] (string date)
  - other fields: direct overwrite
- On parse failure: treats entire file as content with empty frontmatter
- UNCLEAR: `cfg.inject` field is defined in FrontmatterConfig but never checked — always injects

**`FileWriter(vault_cfg: VaultConfig, cfg: WriterConfig)`**
- `update(path: Path, existing_content: str, new_content: str) -> None`
  - append_position="end": `existing_content + "\n\n---\n\n" + new_content`
  - append_position="after_last_heading": inserts after last heading match in content
  - Writes via atomic_write (temp file + os.replace)
- `create(folder: str, filename: str, content: str) -> Path`
  - Creates `vault_path / folder / _sanitize_filename(filename)`
  - `_sanitize_filename`: replaces `<>:"/\|?*` with `-`, appends `.md` if missing
  - mkdir parents=True, exist_ok=True
  - Returns path of created file

**`IndexTrigger(indexer_factory: callable)`**
- `reindex(path: Path) -> None`
- Calls factory to get (loader, splitter, writer, manifest) — fresh instances per call
- Runs full single-file index: DocumentLoader.load() -> TextSplitter.split() -> IndexWriter.write() -> ManifestManager.update()
- ONLY the single written file is reindexed — not the whole vault
- On failure: exception propagates. File is already written (write happened before reindex). Manifest will be stale until next `mdcore index`.

**`atomic_write(path: Path, content: str) -> None`** (from utils/file_utils.py, used by FileWriter)
- Creates temp file in same directory, writes content, `os.replace()` temp -> target
- On failure: temp file unlinked. Original file untouched if not yet replaced.

### Execution path (Flow B write stage)

1. BackupManager.backup(existing_path) — copies existing file
2. FrontmatterInjector.inject(existing_path, fm_updates) — reads file, returns updated string
3. FileWriter.update(path, injected_content, summary_text) OR FileWriter.create(folder, filename, summary) — atomic write
4. IndexTrigger.reindex(path) — single-file reindex

### Side effects
- Reads target file (FrontmatterInjector, BackupManager)
- Writes ~/.mdcore/backups/*.bak (BackupManager)
- Writes/creates vault .md files (FileWriter via atomic_write)
- Writes ChromaDB (IndexTrigger -> IndexWriter)
- Writes manifest.json (IndexTrigger -> ManifestManager)
- Writes embed_cache.pkl (IndexTrigger -> EmbeddingEngine)

### Gotchas
- BackupManager rotation: if backup fails (disk full, permissions), exception propagates and PREVENTS the write. Backup failure = no vault write.
- FrontmatterInjector returns a string — FileWriter.update() receives this as `existing_content` parameter. The returned content already includes frontmatter YAML block. FileWriter then appends summary_text to this.
- IndexTrigger.reindex() failure leaves file written but manifest stale. `mdcore status` will show file as "modified" and `mdcore index` will reindex it.
- FileWriter.update() with append_position="after_last_heading": if target has no headings, appends at end. Does not validate heading levels.
- `create()` with empty folder string: creates file at vault root.

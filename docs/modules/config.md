## mdcore/config/ — Configuration Module

### Purpose
Pydantic config models and YAML loader for MdCoreConfig. Handles config file discovery, models.yaml overlay, path resolution (relative -> absolute), and validation error formatting.

### Public interface

**`load_config(config_path: Optional[str] = None, models_path: Optional[str] = None) -> MdCoreConfig`** (from loader.py)
- `config_path`: if None, uses `~/.mdcore/config.yaml`
- `models_path`: if None, tries `~/.mdcore/models.yaml`; only applied if file exists
- Raises `FileNotFoundError` if config file not found
- Raises `SystemExit` (not exception) if Pydantic validation fails — prints formatted errors then exits
- Config layering: models.yaml `llm` section is SHALLOW-MERGED on top of config.yaml `llm` section. Same for `embeddings`. All other sections come from config.yaml only.
- Resolves relative paths: `vector_store.persist_path` and `manifest.path` are resolved relative to vault root if not absolute

**`expand_path(p: str) -> Path`** (from loader.py)
- `Path(os.path.expandvars(p)).expanduser()` — handles both `~` and `$VAR` expansion

**`DEFAULT_CONFIG_PATH`** — `Path("~/.mdcore/config.yaml").expanduser()`
**`DEFAULT_MODELS_PATH`** — `Path("~/.mdcore/models.yaml").expanduser()`

### MdCoreConfig (Pydantic BaseModel)

Top-level model. All sub-configs have defaults except `vault` (required):
- vault: VaultConfig (REQUIRED)
- indexer: IndexerConfig = default
- embeddings: EmbeddingsConfig = default
- vector_store: VectorStoreConfig = default
- retriever: RetrieverConfig = default
- ingester: IngesterConfig = default
- writer: WriterConfig = default
- llm: LLMConfig = default
- manifest: ManifestConfig = default
- cli: CLIConfig = default
- logging: LoggingConfig = default

### Config layering detail

```python
# In loader.py:
if "llm" in models_raw:
    raw["llm"] = {**(raw.get("llm") or {}), **models_raw["llm"]}
if "embeddings" in models_raw:
    raw["embeddings"] = {**(raw.get("embeddings") or {}), **models_raw["embeddings"]}
```
models.yaml `llm` keys override config.yaml `llm` keys. Unset keys from config.yaml are preserved. Sections other than `llm` and `embeddings` cannot be set via models.yaml.

### Dead config fields (defined but not read anywhere in code)

These fields are in the Pydantic models but have no effect:
- `IndexerConfig.manifest_path` — manifest path comes from ManifestConfig.path
- `RetrieverConfig.output_format` — not read in context_formatter.py
- `RetrieverConfig.include_similarity_scores` — not read in context_formatter.py
- `RetrieverConfig.signpost_include_section_hints` — not read in context_formatter.py
- `WriterConfig.require_approval` — approval always requested in ingest() command
- `FrontmatterConfig.inject` — FrontmatterInjector always injects
- `CLIConfig.theme` — Rich Console created without theme parameter
- `CLIConfig.show_similarity_scores` — not read in any output code
- `CLIConfig.verbose` — verbose is a per-command CLI flag, not read from config
- `LoggingConfig.enabled` — logging always set up in _load()
- `ManifestConfig.drift_warning_age_hours` — never read

### Side effects
- Reads config.yaml from disk (load_config)
- Reads models.yaml from disk if it exists (load_config)
- Exits process on validation error (SystemExit)

### Gotchas
- Validation failure raises SystemExit (not a catchable Python exception in normal code). It IS caught in config_cmd() --validate via `except SystemExit as exc`.
- models.yaml merge is shallow: if models_raw["llm"] = {"model": "x"}, the entire llm config becomes `{...config_llm, "model": "x"}`. This is intentional for model overrides.
- Relative path resolution: if `vector_store.persist_path = ".mdcore-index/chroma_db"`, it becomes `<vault_path>/.mdcore-index/chroma_db`. If the path is already absolute (starts with `/` or `~/`), it is left as-is.
- MCP server and REST server load config at MODULE IMPORT TIME using DEFAULT_CONFIG_PATH — if config is missing when the server module is imported, `FileNotFoundError` is raised before any request handling.

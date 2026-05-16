## mdcore/cli/ — CLI Module

### Purpose
Contains all Typer commands and Rich UI rendering for mdcore. This is the sole entry point for the CLI tool. No business logic lives here — it wires together modules from core/, llm/, store/, and config/.

### Entry point
`pyproject.toml` defines: `mdcore = "mdcore.cli.commands:app"`. `app` is a `typer.Typer` instance created at module level.

### Public interface
`app` — the Typer application. All other symbols in commands.py are internal.

### Commands (all functions in commands.py)

**`init(config)`** — Interactive setup wizard. Detects Ollama, detects RAM, suggests models. Writes `~/.mdcore/config.yaml` via `_write_init_config()`. Does NOT load config at start (no config required). Offers to run `mdcore deps install` after write.

**`index(config, models, verbose, inspect, force)`** — Delta indexer. Loads config. If `--force`: deletes manifest.json, chroma_db/, embed_cache.pkl via `shutil.rmtree` and `Path.unlink()`. Runs VaultScanner.scan() → ManifestManager.diff() → shows diff table → prompts [A]ll/[C]ancel if `confirm_before_index=True` → loops files: DocumentLoader.load() or MultiModalLoader.load() → TextSplitter.split() → IndexWriter.write() → ManifestManager.update(). Exceptions per-file are caught, logged, and shown as warnings — indexing continues. `--inspect <filename>` shows chunk breakdown table for a matching file, then returns without indexing.

**`search(topic, config, models, verbose, raw)`** — Flow A. KeywordPreFilter → VectorSearcher → group_by_source → stitch → rank_sources → assemble → (if `--raw`: format_context) or (if synthesised: raw_text_for_synthesis → LLMLayer.synthesise()). Writes to `<vault>/mdcore-output/<YYYY-MM-DD>-<slug>.md`. Filename from `_query_slug()`: lowercase, hyphens, max 60 chars.

**`ingest(file, config, models)`** — Flow B. SummaryReceiver → SummaryEmbedder → ClassificationEngine → ConflictDetector (if update) → FolderRouter (if new) → ProposalGenerator → `_render_proposal()` → user prompts → `_execute_write()`.

**`vault_map_cmd()` (name: `map`)** — VaultMap.write_template() or (--repair) VaultMap.stale_descriptions() + removes.

**`status(config, models)`** — VaultScanner + ManifestManager + VectorStore.all_metadata(). Displays Rich table. If aggregator backend: shows active key via `llm_keypool.AggregatorChat.current_key()`.

**`eval(topic, config, models)`** — Runs full retrieval pipeline WITHOUT LLM call, prints formatted context, then prints evaluation checklist. If no topic provided, shows checklist only.

**`deps(action, config, models)`** — `status`: shows DepStatus table. `install`: pip installs missing packages into current sys.executable.

**`docs(topic)`** — Reads markdown from `mdcore.docs` package via `importlib.resources.files()`. Topics: config, getting-started, retrieval (with aliases).

**`config_cmd()` (name: `config`)** — Opens config in $EDITOR (default: vi). If `--validate`: calls `load_config()`, prints result or SystemExit message.

**`gui(config)`** — Imports `mdcore.gui.app.run`. Fails with install hint if textual not installed.

**`serve(host, port, config, reload)`** — Starts uvicorn on `mdcore.serve.server:app`. Sets MDCORE_CONFIG_PATH env var if --config provided.

**`mcp(config)`** — `asyncio.run(mdcore.mcp_server.server.main())` — stdio transport. Sets MDCORE_CONFIG_PATH env var.

**`mcp_serve(host, port, config)` (name: `mcp-serve`)** — `mdcore.mcp_server.server.run_sse(host, port)` — SSE/HTTP transport.

### Configuration threading
`_cfg_option` and `_models_option` are module-level `typer.Option` definitions. Each command accepts `config: Optional[str]` and calls `_load(config, models)` which calls `load_config()` + `setup_logging()`. Config is loaded fresh per command invocation, not at app startup.

### Internal helpers
- `_load(config, models)` → MdCoreConfig
- `_make_store(cfg)` → VectorStore
- `_make_engine(cfg)` → EmbeddingEngine
- `_query_slug(topic)` → safe filename
- `_render_proposal(proposal)` → Rich panel
- `_execute_write(cfg, proposal, summary, existing_content)` → runs writer pipeline
- `_derive_filename(summary)` → extracts H1/H2/H3 from summary for filename
- `_show_inspect(filename, loader, splitter, vault_cfg)` → chunk inspection table
- Hardware detection helpers: `_detect_ollama_models()`, `_detect_ram_gb()`, `_is_apple_silicon()`, `_hardware_label()`, `_suggest_primary_model()`, `_suggest_synth_model()`, `_suggest_embed_model()`

### Side effects
- Writes `~/.mdcore/config.yaml` (init)
- Writes `<vault>/mdcore-output/*.md` (search)
- Writes/updates vault .md files (ingest approve)
- Deletes manifest.json, chroma_db/, embed_cache.pkl (index --force)
- Reads vault files (index, search, ingest)

### Gotchas
- `mdcore-output` is hardcoded as `<vault>/mdcore-output/` — not configurable.
- `init()` does not validate vault path exists — only warns.
- Exception during single file index: caught and skipped, indexing continues. Errors visible in output and logs.
- `eval()` does NOT call the LLM — it is a manual quality check tool.
- `mcp()` must not print to stdout — any stdout output corrupts the MCP stdio protocol.
- `serve()` and `mcp_server/server.py` load config at module import time (server.py lines 18-20, 34-36) — if config is missing at server start, ImportError-level crash occurs before request handling.

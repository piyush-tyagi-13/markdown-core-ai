# mdcore

**Markdown CORE AI - Classification, Organisation, Retrieval & Entry**

`mdcore` is a local, LLM-agnostic knowledge base engine for anyone with a folder of markdown notes. It reads and writes your vault intelligently - retrieve context on demand, ingest new knowledge with automatic classification and routing, all from the terminal or a TUI.

**PyPI:** `markdowncore-ai` | **CLI:** `mdcore` | **Version:** 1.1.0

---

## Screenshots

![mdcore home](assets/home.jpg)

![mdcore search](assets/search.jpg)

![mdcore index](assets/index_vault.jpg)

![mdcore status](assets/status.jpg)

---

## What It Does

**Retrieval (`mdcore search`)** - Ask a question or give a topic. mdcore searches your vault semantically, stitches the most relevant chunks, and synthesises a coherent cited briefing. Output lands in `<vault>/mdcore-output/` - ready to copy into any LLM conversation.

**Ingestion (`mdcore ingest`)** - Feed any document into mdcore - an LLM session summary, a research note, a strategy doc, an article. It classifies the content against your existing vault, routes it to the right folder, detects conflicts with existing notes, generates a proposal, and writes only after your explicit approval.

Both flows work fully local with Ollama. No subscription LLM API calls. No always-on server.

---

## Installation

```bash
# Recommended - with TUI
uv tool install "markdowncore-ai[gui]"

# pipx
pipx install markdowncore-ai
```

### Using the aggregator backend (free-tier, no paid API)

Install [llm-keypool](https://pypi.org/project/llm-keypool/) separately - it has its own CLI for managing keys:

```bash
# Install llm-keypool as standalone tool (gives llm-keypool CLI)
uv tool install "llm-keypool[gui]"

# Also wire it into mdcore's environment so mdcore can import it
uv tool install --force "markdowncore-ai[gui]" --with llm-keypool
```

### Upgrading

```bash
# Upgrade mdcore
uv tool upgrade markdowncore-ai

# Upgrade llm-keypool + rewire into mdcore
uv tool upgrade llm-keypool
uv tool install --force "markdowncore-ai[gui]" --with llm-keypool
```

### Ollama models (local inference)

```bash
ollama pull nomic-embed-text   # embeddings
ollama pull qwen3.5:4b         # classification, routing, proposals
ollama pull phi4-mini          # synthesis (fast, non-thinking)
```

### First run

```bash
mdcore init     # interactive setup -> writes ~/.mdcore/config.yaml
mdcore index    # scan and index your vault
```

---

## Quick Start

```bash
# Search your vault
mdcore search "kubernetes ingress routing"
# -> synthesised briefing written to <vault>/mdcore-output/
# -> copy contents, paste into Claude / ChatGPT / Gemini

# Ingest a document
mdcore ingest --file my-session-summary.md
# -> classifies, routes to right folder, proposes changes -> approve to write

# Launch TUI
mdcore gui
```

---

## Commands

```bash
mdcore init                        # Interactive setup wizard
mdcore index                       # Delta index - scan, diff, confirm, index
mdcore index --force               # Wipe everything and reindex from scratch
mdcore search <topic>              # Retrieve + synthesise briefing (Flow A)
mdcore search <topic> --raw        # Retrieve raw excerpts, skip synthesis
mdcore search <topic> --verbose    # Show similarity scores
mdcore ingest                      # Paste document - classify, route, propose (Flow B)
mdcore ingest --file <path>        # Ingest from file
mdcore map                         # Generate vault folder map for routing
mdcore map --repair                # Remove stale folder entries
mdcore gui                         # Launch TUI (requires [gui] extra)
mdcore status                      # Index health, drift warnings
mdcore eval [topic]                # Retrieval quality checklist
mdcore config                      # Open config in editor
mdcore config --validate           # Validate config
mdcore serve                           # Start REST API server (requires [serve])
mdcore mcp                             # Start MCP server over stdio (requires [mcp])
```

### Multiple vaults / config profiles

```bash
mdcore search "istio auth"     --config ~/.mdcore/config-work.yaml
mdcore search "career goals"   --config ~/.mdcore/config-personal.yaml
mdcore search "topic"          --models ~/.mdcore/models-aggregator.yaml
```

---

## Backends

mdcore supports local and API-backed models. Mix and match per use case.

| Backend | LLM | Embeddings | Extra needed |
|---|---|---|---|
| Ollama (local) | any pulled model | `nomic-embed-text`, `bge-m3` | none |
| Gemini | `gemini-2.5-flash-lite` | `models/gemini-embedding-001` | none (bundled) |
| OpenAI | `gpt-4o-mini` | `text-embedding-3-small` | `[openai]` |
| Anthropic | `claude-haiku-4-5` | use Ollama or OpenAI | `[anthropic]` |
| Aggregator | free-tier key pool | free-tier key pool | `llm-keypool` (separate) |

```bash
uv tool install "markdowncore-ai[openai]"
uv tool install "markdowncore-ai[anthropic]"
uv tool install "markdowncore-ai[all]"           # every backend
uv tool install "markdowncore-ai[multimodal]"    # PDF, DOCX, TXT indexing
uv tool install "markdowncore-ai[serve]"         # REST API server
uv tool install "markdowncore-ai[mcp]"           # MCP server for Claude Desktop
```

### Aggregator backend

`aggregator` routes calls through [llm-keypool](https://github.com/piyush-tyagi-13/llm-keypool) - a local SQLite-backed key pool that round-robins free-tier API keys with automatic 429 cooldown. No `api_key` needed in mdcore config.

> Note: `aggregator` is for LLM calls only. Embeddings require a dedicated backend (ollama, openai, or gemini) - embedding models cannot be swapped mid-index.

**Install llm-keypool separately** (required - it has its own CLI for managing keys):

```bash
# Install as standalone tool so its CLI is available system-wide
uv tool install "llm-keypool[gui]"

# Also add it to mdcore's environment so mdcore can import it
uv tool install --force "markdowncore-ai[gui]" --with llm-keypool
```

Upgrading llm-keypool:

```bash
uv tool upgrade llm-keypool
uv tool install --force "markdowncore-ai[gui]" --with llm-keypool
```

Keys DB lives at `~/.llm-keypool/keys.db`. Register free-tier keys:

```bash
# Groq - https://console.groq.com/keys
llm-keypool add --provider groq --key <KEY> --model llama-3.3-70b-versatile --category general_purpose

# Cerebras - https://cloud.cerebras.ai
llm-keypool add --provider cerebras --key <KEY> --model llama-3.3-70b --category general_purpose

# Mistral - https://console.mistral.ai/api-keys
llm-keypool add --provider mistral --key <KEY> --model mistral-small-latest --category general_purpose

# OpenRouter - https://openrouter.ai/settings/keys
llm-keypool add --provider openrouter --key <KEY> --model meta-llama/llama-3.3-70b-instruct:free --category general_purpose

# Check registered keys
llm-keypool status
```

```yaml
llm:
  backend: aggregator
  aggregator_category: general_purpose
  aggregator_rotate_every: 5

embeddings:
  backend: ollama        # aggregator not supported for embeddings
  local_model: nomic-embed-text
```

### Hardware guidance

| Hardware | LLM | Embeddings |
|---|---|---|
| Apple M2 16GB+ | `qwen3.5:4b` | `nomic-embed-text` |
| i5 + RTX 4070 | `qwen3:8b` | `bge-m3` |
| Low-end / no GPU | `gemini-2.5-flash-lite` or `gpt-4o-mini` | `models/gemini-embedding-001` |

---

## Configuration

Config lives at `~/.mdcore/config.yaml`. Generated by `mdcore init`.

| Section | Key fields | Purpose |
|---|---|---|
| `vault` | `path`, `owner_name` | Vault root, owner name for multi-person vaults |
| `embeddings` | `backend`, `api_model` / `local_model`, `api_key` | Embedding model |
| `llm` | `backend`, `model`, `api_key`, `synthesise_model` | Primary LLM + synthesis model |
| `indexer` | `chunk_size`, `heading_aware_splitting` | Chunking strategy |
| `retriever` | `top_k`, `similarity_threshold` | Retrieval tuning |
| `ingester` | `similarity_threshold_high/low` | Classification thresholds |
| `writer` | `append_position`, `backup` | Write behaviour + backups |

See `config.yaml.example` for the full annotated reference.

### Separate models config

Keep model choices in a separate `~/.mdcore/models.yaml` - useful for switching backends without touching main config. Values here override `llm` and `embeddings` sections in `config.yaml`.

```yaml
# ~/.mdcore/models.yaml
llm:
  backend: aggregator
  aggregator_category: general_purpose

embeddings:
  backend: ollama
  local_model: nomic-embed-text
```

Pass explicitly with `--models`:

```bash
mdcore search "topic" --models ~/.mdcore/models-work.yaml
mdcore ingest --file note.md --models ~/.mdcore/models-cheap.yaml
```

---

## Where LLM Calls Happen

### `mdcore search` (Flow A)

| Phase | LLM? | Notes |
|---|---|---|
| Keyword pre-filter | No | BM25 scoring |
| Vector search | No | Embedding lookup |
| Chunk assembly | No | Pure text |
| **Synthesis** | **Yes** - `synthesise_model` | Skip with `--raw` for zero LLM calls |

### `mdcore ingest` (Flow B)

| Phase | LLM? | Condition |
|---|---|---|
| Embedding + search | No | Always |
| **Classification** | **Conditional** - `llm.model` | Only in ambiguous similarity range (0.65-0.82) |
| **Folder routing** | **Yes** - `llm.model` | NEW files only |
| **Proposal** | **Yes** - `llm.model` | Always before write |

`mdcore map` and `mdcore index` make no LLM calls.

---

## Multi-Modal Indexing

By default mdcore indexes `.md` files only. Enable additional formats in `~/.mdcore/config.yaml`:

```yaml
vault:
  index_pdf: true    # PDF text extraction (text-based PDFs; scanned PDFs return no text)
  index_docx: true   # Word documents (.docx only, not legacy .doc)
  index_txt: true    # Plain text files
```

Requires the `[multimodal]` extra:

```bash
pip install 'markdowncore-ai[multimodal]'
# or
uv tool install "markdowncore-ai[multimodal]"
```

Once enabled, run `mdcore index` as normal - PDF/DOCX/TXT files appear in `mdcore status` and are searchable via `mdcore search`.

**Limitations:**
- Scanned PDFs (image-only) yield no text - extraction requires selectable text layers
- `.doc` (legacy binary Word format) is not supported, only `.docx`


---

## REST API

Start the HTTP server to expose vault search and ingestion as JSON endpoints:

```bash
pip install 'markdowncore-ai[serve]'
mdcore serve                          # default: http://127.0.0.1:8765
mdcore serve --host 0.0.0.0 --port 9000
mdcore serve --reload                 # dev mode, auto-reload on code change
```

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Index health - chunk count, vault path |
| `POST` | `/ask` | Ask a question, get synthesised answer + sources |
| `POST` | `/propose` | Classify and propose ingestion (no write) |
| `POST` | `/search/invoke` | LangServe chain endpoint |
| `GET` | `/docs` | Swagger UI |

### Examples

```bash
# Health check
curl http://127.0.0.1:8765/health

# Ask a question
curl -X POST http://127.0.0.1:8765/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "what is the mTLS topology for the COE stack?"}'

# Propose ingestion (returns proposal, does not write)
curl -X POST http://127.0.0.1:8765/propose \
  -H "Content-Type: application/json" \
  -d '{"content": "Meeting notes from arch review...", "title": "Arch Review 2025-05"}'

# LangServe invoke
curl -X POST http://127.0.0.1:8765/search/invoke \
  -H "Content-Type: application/json" \
  -d '{"input": {"query": "kubernetes ingress"}}'
```

OpenAPI spec: [`docs/openapi.json`](docs/openapi.json) - import into Postman, Insomnia, or any OpenAPI-compatible client.

The chain implementation uses `RunnableLambda` wrapping the existing two-phase retrieval pipeline - the LangServe layer adds HTTP transport without replacing mdcore's BM25 pre-filter or vector search.


---

## MCP Server (Claude Desktop Integration)

mdcore exposes its vault as MCP tools that Claude Desktop (and any MCP-compatible client) can call autonomously during a conversation.

```bash
pip install 'markdowncore-ai[mcp]'
```

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "mdcore": {
      "command": "mdcore",
      "args": ["mcp"],
      "env": {
        "MDCORE_CONFIG_PATH": "/Users/you/.mdcore/config.yaml"
      }
    }
  }
}
```

Restart Claude Desktop. mdcore appears as a connected tool. Ask Claude:
- *"What do my notes say about the payments architecture?"*
- *"Save this meeting summary to my vault"*

### Tools exposed

| Tool | Description |
|---|---|
| `search_vault` | Search vault, return synthesised answer with cited sources |
| `ingest_note` | Classify content and propose where to save it (does not write automatically) |
| `vault_status` | Current index stats - chunk count, file types, backends |

### Multiple vaults

Expose separate work and personal vaults as distinct tools by running two MCP server processes with different `--config` paths, or configure vault-scoped tool variants directly in `mcp_server/server.py`.

### Smoke test (without Claude Desktop)

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | mdcore mcp
```


---

## Observability

Token usage logged after every call to `~/.mdcore/logs/`:
```
INFO llm - tokens [gemini-2.5-flash-lite] in=312 out=89 total=401
```

LangSmith tracing (optional) - add to `~/.mdcore/config.yaml`:
```yaml
llm:
  langsmith_api_key: <your-key>
  langsmith_project: mdcore
```

---

*mdcore - Markdown CORE AI v1.1.0*

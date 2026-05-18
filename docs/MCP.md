# mdcore MCP Server

mdcore exposes a Model Context Protocol (MCP) server so AI agents (Hermes, Claude Desktop, etc.) can search your vault, check index status, and trigger indexing — all without leaving the agent chat.

---

## Transport

Two transports are available depending on the client:

| Transport | How it works | When to use |
|---|---|---|
| **stdio** | Client spawns `mdcore mcp` as a subprocess on demand. No background server needed. | Claude Desktop, most MCP clients |
| **SSE / HTTP** | `mdcore mcp --sse` starts a persistent HTTP server at `http://127.0.0.1:8766/sse`. | Hermes agent, any HTTP-based MCP client |

The stdio transport is the default. The MCP process loads config, builds the search and ingest chains, and then listens on stdin/stdout for JSON-RPC messages. It writes logs to a rotating file only — stdout stays clean for the protocol.

---

## Startup Sequence

When the MCP server starts (either transport), it immediately:

1. Loads `~/.mdcore/config.yaml` (and `~/.mdcore/models.yaml` if present)
2. Builds `_search_chain` via `build_search_chain(cfg)`
3. Builds `_ingest_chain` via `build_ingest_chain(cfg)`
4. Registers tool handlers and begins serving

Both chains are initialised once at startup and reused across all requests. This means the first tool call is cold (loads embedding engine, connects to ChromaDB); subsequent calls are warm.

---

## Tools

### `search_vault`

Search the vault and return a synthesised briefing.

**Input:**
```json
{ "query": "how does Ally Vibe Control persist settings?" }
```

**What happens internally:**
1. `_handle_search` calls `_search_chain.invoke({"query": query})`
2. Chain runs: KeywordPreFilter → VectorSearcher → ChunkStitcher → ContextAssembler → LLMLayer.synthesise()
3. Returns `answer` (synthesised briefing) + `sources` (list of vault file paths cited)

**Output:**
```
Ally Vibe Control persists settings via SettingsManager, which writes
to a JSON config file on Decky's plugin data path at startup. [1]

**Sources:** Personal Projects/Ally Vibe Control/plugin-notes.md
```

**Use when:** User asks about their notes, decisions, projects, meetings, or anything that might be in the vault.

---

### `ingest_note`

Classify a note and get a write proposal — does NOT write to disk.

**Input:**
```json
{
  "content": "# Java Streams\n\n## map vs flatMap\n...",
  "title": "Java Streams Reference"
}
```

**What happens internally:**
1. `_handle_ingest` calls `_ingest_chain.invoke({"content": ..., "title": ...})`
2. Chain runs: SummaryReceiver → SummaryEmbedder → ClassificationEngine → FolderRouter (if new) → ConflictDetector (if update) → ProposalGenerator
3. Returns classification result: action, target file, folder, confidence, proposal text

**Output:**
```
**Ingestion Proposal**

Action: new
Target: New file
Folder: Java/Core Concepts
Confidence: 84%

**Proposed changes:**
[full proposed document text]

_Run `mdcore ingest` in your terminal to review and approve the write._
```

**Important:** This tool only proposes. The user must run `mdcore ingest` in the terminal to actually write the file. The agent should never assume the write happened.

---

### `vault_status`

Returns index health snapshot.

**Input:** none

**What happens internally:**
1. Opens ChromaDB and counts all stored chunk metadata
2. Breaks down chunk count by file type
3. Reports vault path, embedding backend, LLM backend from config

**Output:**
```
**mdcore Vault Status**

Vault: /Users/piyush/vault
Indexed chunks: 842
File types: 229 md
Embedding backend: gemini
LLM backend: aggregator
```

---

### `index_vault`

Scan vault for changes and optionally reindex.

**Input:**
```json
{ "dry_run": true }
```

**Two-step confirmation flow (enforced by tool description):**

**Step 1 — dry run (default):**
- Runs VaultScanner + ManifestManager.diff()
- Returns counts of new, modified, and deleted files
- Does NOT write anything

```
**Vault Index Summary**

Total eligible files: 231
New (unindexed):      3
Modified (stale):     1
Deleted:              0
Delta total:          4 files will change

_Dry run only. Call index_vault with dry_run=false to actually index._
```

**Step 2 — actual index (requires explicit user approval):**
```json
{ "dry_run": false }
```
- Loads, splits, embeds, and upserts each new/modified file
- Deletes entries for removed files
- Reports indexed, deleted, skipped counts

```
**Indexing complete**

Indexed:  4 files
Deleted:  0 entries removed
Skipped:  0 files (errors)
```

The tool description explicitly instructs the LLM: _"Never call with dry_run=false without explicit user approval."_

---

## Implementation Details

### Async execution

All tool handlers are `async` but the underlying chains (search, ingest, index) are synchronous. Handlers use `loop.run_in_executor(None, ...)` to run sync code in a thread pool, keeping the MCP event loop unblocked.

### Config at startup

Config is loaded once at module level (`_cfg = load_config(...)`). If you change `~/.mdcore/config.yaml`, restart the MCP server to pick up changes. For stdio transport this is automatic (client respawns the process); for SSE you need to stop and restart `mdcore mcp --sse`.

### No persistent state between calls

Each tool call operates on the already-loaded chains. The VectorStore (ChromaDB) is persistent on disk and shared across calls, so a `search_vault` call immediately after `index_vault` will see the newly indexed content.

---

## Client Configuration

### Claude Desktop (`~/.claude/claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "mdcore": {
      "command": "mdcore",
      "args": ["mcp"]
    }
  }
}
```

No background server needed. Claude Desktop spawns the process on demand.

### Hermes Agent (`~/.hermes/config.yaml`)

```yaml
mcp_servers:
  mdcore:
    transport: sse
    url: http://127.0.0.1:8766/sse
```

Start the SSE server before using Hermes: `mdcore mcp --sse`

### Requirements

The LLM backend must support function calling (tool use). The `aggregator` backend (llm-keypool) strips the `tools` parameter — use a direct provider instead (Google Gemini, OpenAI, Anthropic, or Ollama with a capable model).

---

## Source

All MCP logic lives in a single file: `mdcore/mcp_server/server.py`

```
server.py
├── list_tools()          # Registers all 4 tools with schemas
├── call_tool()           # Dispatches by tool name
├── _handle_search()      # Calls _search_chain
├── _handle_ingest()      # Calls _ingest_chain
├── _handle_status()      # Reads VectorStore metadata
├── _handle_index()       # Runs VaultScanner + ManifestManager + IndexWriter
├── main()                # stdio entry point
└── run_sse()             # SSE/HTTP entry point
```

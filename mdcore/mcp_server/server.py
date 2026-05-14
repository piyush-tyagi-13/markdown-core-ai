from __future__ import annotations
import asyncio
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from pathlib import Path

from mdcore.config.loader import load_config, DEFAULT_CONFIG_PATH
from mdcore.serve.chain import build_search_chain, build_ingest_chain
from mdcore.store.vector_store import VectorStore

server = Server("mdcore")

_cfg = load_config(DEFAULT_CONFIG_PATH)
_search_chain = build_search_chain(_cfg)
_ingest_chain = build_ingest_chain(_cfg)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_vault",
            description=(
                "Search the user's personal knowledge base vault and return a synthesised "
                "briefing on the topic. Use this when the user asks about their own notes, "
                "projects, decisions, meeting summaries, or any topic that might be in their "
                "personal knowledge base. Returns cited excerpts from relevant documents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The question or topic to search for. "
                            "Be specific -- 'istio mTLS COE stack' works better than 'security'."
                        ),
                    }
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="ingest_note",
            description=(
                "Classify a new note or document and get a proposal for where it should be "
                "saved in the vault. Use this when the user wants to add new content to their "
                "knowledge base. Returns a proposal -- does NOT write to disk automatically."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The full text content of the note to ingest.",
                    },
                    "title": {
                        "type": "string",
                        "description": "A short title for the note.",
                    },
                },
                "required": ["content"],
            },
        ),
        types.Tool(
            name="vault_status",
            description=(
                "Get the current status of the vault index: how many documents are indexed, "
                "which file types are enabled, and when the index was last updated."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="index_vault",
            description=(
                "Scan the vault for new or modified files and optionally reindex them. "
                "Call with dry_run=true first to get a summary of what would change (counts of new, "
                "modified, and deleted files). Show the summary to the user and ask for confirmation "
                "before calling again with dry_run=false to actually run the indexing. "
                "Never call with dry_run=false without explicit user approval."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dry_run": {
                        "type": "boolean",
                        "description": (
                            "If true (default), scan and return a summary without writing anything. "
                            "If false, actually index all new/modified files."
                        ),
                        "default": True,
                    }
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name == "search_vault":
        return await _handle_search(arguments)
    elif name == "ingest_note":
        return await _handle_ingest(arguments)
    elif name == "vault_status":
        return await _handle_status()
    elif name == "index_vault":
        return await _handle_index(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


async def _handle_search(args: dict[str, Any]) -> list[types.TextContent]:
    query = args.get("query", "")
    if not query:
        return [types.TextContent(type="text", text="Error: query is required.")]

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _search_chain.invoke({"query": query}),
    )

    answer = result.get("answer", "No answer generated.")
    sources = result.get("sources", [])
    output = answer
    if sources:
        output += f"\n\n**Sources:** {', '.join(sources)}"

    return [types.TextContent(type="text", text=output)]


async def _handle_ingest(args: dict[str, Any]) -> list[types.TextContent]:
    content = args.get("content", "")
    title = args.get("title", "Untitled")

    if not content:
        return [types.TextContent(type="text", text="Error: content is required.")]

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _ingest_chain.invoke({"content": content, "title": title}),
    )

    output = (
        f"**Ingestion Proposal**\n\n"
        f"Action: {result.get('action', 'unknown')}\n"
        f"Target: {result.get('target_file') or 'New file'}\n"
        f"Folder: {result.get('suggested_folder') or 'TBD'}\n"
        f"Confidence: {result.get('confidence', 0):.0%}\n\n"
        f"**Proposed changes:**\n{result.get('proposal', '')}\n\n"
        f"_Run `mdcore ingest` in your terminal to review and approve the write._"
    )

    return [types.TextContent(type="text", text=output)]


async def _handle_status() -> list[types.TextContent]:
    store = VectorStore(_cfg.vector_store)
    meta = store.all_metadata()
    chunk_count = len(meta)

    file_types: dict[str, int] = {}
    for m in meta:
        ft = m.get("file_type", "md")
        file_types[ft] = file_types.get(ft, 0) + 1

    type_summary = ", ".join(f"{v} {k}" for k, v in file_types.items())

    output = (
        f"**mdcore Vault Status**\n\n"
        f"Vault: {_cfg.vault.path}\n"
        f"Indexed chunks: {chunk_count}\n"
        f"File types: {type_summary or 'unknown'}\n"
        f"Embedding backend: {_cfg.embeddings.backend}\n"
        f"LLM backend: {_cfg.llm.backend}\n"
    )

    return [types.TextContent(type="text", text=output)]


async def _handle_index(args: dict[str, Any]) -> list[types.TextContent]:
    dry_run = args.get("dry_run", True)

    loop = asyncio.get_event_loop()

    def _scan_diff():
        from mdcore.core.indexer.vault_scanner import VaultScanner
        from mdcore.core.indexer.manifest_manager import ManifestManager

        scanner = VaultScanner(_cfg.vault, _cfg.indexer)
        manifest = ManifestManager(_cfg.manifest, _cfg.vault)
        eligible = scanner.scan()
        diff = manifest.diff(eligible)
        return eligible, diff

    eligible, diff = await loop.run_in_executor(None, _scan_diff)

    summary = (
        f"**Vault Index Summary**\n\n"
        f"Total eligible files: {len(eligible)}\n"
        f"New (unindexed):      {len(diff.new_files)}\n"
        f"Modified (stale):     {len(diff.modified_files)}\n"
        f"Deleted:              {len(diff.deleted_files)}\n"
        f"Delta total:          {diff.total_changes} files will change\n"
    )

    if diff.total_changes == 0:
        return [types.TextContent(type="text", text="Index is up to date - nothing to do.")]

    if dry_run:
        return [types.TextContent(
            type="text",
            text=summary + "\n_Dry run only. Call index_vault with dry_run=false to actually index._",
        )]

    # Actually index
    def _run_index():
        from mdcore.core.indexer.vault_scanner import VaultScanner
        from mdcore.core.indexer.manifest_manager import ManifestManager
        from mdcore.core.indexer.document_loader import DocumentLoader
        from mdcore.core.indexer.text_splitter import TextSplitter
        from mdcore.core.indexer.index_writer import IndexWriter
        from mdcore.core.indexer.embedding_engine import EmbeddingEngine

        scanner = VaultScanner(_cfg.vault, _cfg.indexer)
        manifest = ManifestManager(_cfg.manifest, _cfg.vault)
        loader = DocumentLoader(_cfg.vault)
        splitter = TextSplitter(_cfg.indexer)
        store = VectorStore(_cfg.vector_store)
        engine = EmbeddingEngine(_cfg.embeddings)
        writer = IndexWriter(store, engine, _cfg.indexer)

        eligible = scanner.scan()
        diff = manifest.diff(eligible)

        indexed, skipped = 0, 0
        for path in diff.new_files + diff.modified_files:
            try:
                doc = loader.load(path)
                chunks = splitter.split(doc)
                source_file = doc.metadata.get("source_file", str(path))
                writer.write(chunks, source_file)
                manifest.update(path)
                indexed += 1
            except Exception:
                skipped += 1

        for key in diff.deleted_files:
            store.delete(key)

        return indexed, skipped, len(diff.deleted_files)

    indexed, skipped, deleted = await loop.run_in_executor(None, _run_index)

    result = (
        f"**Indexing complete**\n\n"
        f"Indexed:  {indexed} files\n"
        f"Deleted:  {deleted} entries removed\n"
        f"Skipped:  {skipped} files (errors)\n"
    )
    return [types.TextContent(type="text", text=result)]


async def main():
    # Logging writes to file only (RotatingFileHandler) - stdout is clean for MCP protocol
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run_sse(host: str = "127.0.0.1", port: int = 8766) -> None:
    """Run MCP server with SSE/HTTP transport for clients like Hermes."""
    try:
        import uvicorn
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.routing import Mount, Route
    except ImportError:
        raise ImportError(
            "SSE transport requires uvicorn and starlette. "
            "Install with: pip install uvicorn starlette"
        )

    sse = SseServerTransport("/messages")

    async def handle_sse(request: Request) -> None:
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages", app=sse.handle_post_message),
        ]
    )

    import logging
    logging.getLogger("mdcore.mcp_sse").info(
        "MCP SSE server on http://%s:%d/sse", host, port
    )
    uvicorn.run(starlette_app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    asyncio.run(main())

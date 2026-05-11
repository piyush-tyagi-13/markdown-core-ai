from __future__ import annotations
import asyncio
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

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
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name == "search_vault":
        return await _handle_search(arguments)
    elif name == "ingest_note":
        return await _handle_ingest(arguments)
    elif name == "vault_status":
        return await _handle_status()
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


async def main():
    # Logging writes to file only (RotatingFileHandler) - stdout is clean for MCP protocol
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())

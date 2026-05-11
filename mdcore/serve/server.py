from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from mdcore.config.loader import load_config, DEFAULT_CONFIG_PATH
from mdcore.serve.chain import build_search_chain, build_ingest_chain
from mdcore.serve.models import (
    SearchRequest, SearchResponse,
    IngestRequest, IngestResponse,
    HealthResponse,
)
from mdcore.store.vector_store import VectorStore

try:
    from langserve import add_routes
    _LANGSERVE_AVAILABLE = True
except ImportError:
    _LANGSERVE_AVAILABLE = False

app = FastAPI(
    title="mdcore API",
    description="REST API for the mdcore personal knowledge base engine",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_cfg = load_config(DEFAULT_CONFIG_PATH)
_search_chain = build_search_chain(_cfg)
_ingest_chain = build_ingest_chain(_cfg)

if _LANGSERVE_AVAILABLE:
    add_routes(app, _search_chain, path="/search")
    add_routes(app, _ingest_chain, path="/ingest-propose")


@app.get("/health", response_model=HealthResponse)
def health():
    store = VectorStore(_cfg.vector_store)
    meta = store.all_metadata()
    return HealthResponse(
        status="ok",
        vault_path=_cfg.vault.path,
        indexed_chunks=len(meta),
        index_age_hours=None,
    )


@app.post("/ask", response_model=SearchResponse)
def ask(req: SearchRequest):
    try:
        cfg = load_config(req.config_path) if req.config_path else _cfg
        chain = build_search_chain(cfg) if req.config_path else _search_chain
        result = chain.invoke({"query": req.query})
        return SearchResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/propose", response_model=IngestResponse)
def propose(req: IngestRequest):
    try:
        cfg = load_config(req.config_path) if req.config_path else _cfg
        chain = build_ingest_chain(cfg) if req.config_path else _ingest_chain
        result = chain.invoke({"content": req.content, "title": req.title})
        return IngestResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

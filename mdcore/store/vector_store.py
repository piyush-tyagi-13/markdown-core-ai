from __future__ import annotations
from typing import Optional

import chromadb
from chromadb.config import Settings
from langchain_core.documents import Document

from mdcore.config.models import VectorStoreConfig
from mdcore.config.loader import expand_path
from mdcore.utils.logging import get_logger

log = get_logger("store")


class VectorStore:
    def __init__(self, cfg: VectorStoreConfig) -> None:
        persist_path = expand_path(cfg.persist_path)
        persist_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(persist_path),
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=cfg.collection_name,
            metadata={"hnsw:space": cfg.distance_metric},
        )
        log.info("VectorStore ready: collection=%s path=%s", cfg.collection_name, persist_path)

    # ── public API ──────────────────────────────────────────────────────────

    def upsert(self, chunks: list[Document], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        ids = [self._chunk_id(c) for c in chunks]
        docs = [c.page_content for c in chunks]
        metas = [c.metadata for c in chunks]
        self._collection.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
        log.debug("Upserted %d chunks", len(chunks))

    def delete(self, source_file: str) -> None:
        results = self._collection.get(where={"source_file": source_file}, include=[])
        if results["ids"]:
            self._collection.delete(ids=results["ids"])
            log.debug("Deleted %d chunks for %s", len(results["ids"]), source_file)

    def search(
        self,
        query_embedding: list[float],
        k: int,
        filter: Optional[dict] = None,
    ) -> list[Document]:
        kwargs: dict = {"query_embeddings": [query_embedding], "n_results": min(k, self._count())}
        if filter:
            kwargs["where"] = filter
        if kwargs["n_results"] == 0:
            return []
        results = self._collection.query(**kwargs, include=["documents", "metadatas", "distances"])
        docs = []
        for doc, meta, dist in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        ):
            similarity = 1.0 - dist  # ChromaDB cosine returns distance (0=identical)
            m = dict(meta)
            m["_similarity"] = similarity
            docs.append(Document(page_content=doc, metadata=m))
        return docs

    def search_in_sources(
        self,
        query_embedding: list[float],
        source_files: set[str],
        k: int,
    ) -> list[Document]:
        """Vector search restricted to specific source_file values.

        Uses ChromaDB $in filter so only chunks from these files are ranked.
        Used as a fallback for keyword-matched files that score below the main
        similarity threshold.
        """
        if not source_files:
            return []
        where: dict = {"source_file": {"$in": list(source_files)}}
        return self.search(query_embedding, k=k, filter=where)

    def file_embeddings(self) -> dict[str, list[float]]:
        """Return one aggregate (mean) embedding per source file."""
        count = self._count()
        if count == 0:
            return {}
        results = self._collection.get(include=["embeddings", "metadatas"])
        file_vecs: dict[str, list[list[float]]] = {}
        for emb, meta in zip(results["embeddings"], results["metadatas"]):
            sf = meta.get("source_file", "")
            file_vecs.setdefault(sf, []).append(emb)
        aggregated: dict[str, list[float]] = {}
        for sf, vecs in file_vecs.items():
            # Convert numpy arrays to plain lists if necessary
            plain_vecs = [list(map(float, v)) for v in vecs]
            dim = len(plain_vecs[0])
            mean_vec = [sum(v[i] for v in plain_vecs) / len(plain_vecs) for i in range(dim)]
            aggregated[sf] = mean_vec
        return aggregated

    def all_metadata(self) -> list[dict]:
        if self._count() == 0:
            return []
        results = self._collection.get(include=["metadatas"])
        return list(results["metadatas"])

    def all_chunks(self) -> list[Document]:
        """Return every stored chunk as a Document (content + metadata).

        Used by the BM25 keyword pre-filter, which scores over chunk content.
        """
        if self._count() == 0:
            return []
        results = self._collection.get(include=["documents", "metadatas"])
        return [
            Document(page_content=doc, metadata=dict(meta))
            for doc, meta in zip(results["documents"], results["metadatas"])
        ]

    def _count(self) -> int:
        return self._collection.count()

    @staticmethod
    def _chunk_id(chunk: Document) -> str:
        sf = chunk.metadata.get("source_file", "unknown")
        idx = chunk.metadata.get("chunk_index", 0)
        return f"{sf}::chunk::{idx}"

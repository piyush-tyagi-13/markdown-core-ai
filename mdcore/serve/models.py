from __future__ import annotations
from typing import Optional

from pydantic import BaseModel


class SearchRequest(BaseModel):
    query: str
    config_path: Optional[str] = None


class SearchResponse(BaseModel):
    answer: str
    sources: list[str]
    raw_context: str


class IngestRequest(BaseModel):
    content: str
    title: str = "Untitled"
    config_path: Optional[str] = None


class IngestResponse(BaseModel):
    status: str
    action: str
    target_file: Optional[str]
    suggested_folder: Optional[str]
    proposal: str
    confidence: float


class HealthResponse(BaseModel):
    status: str
    vault_path: str
    indexed_chunks: int
    index_age_hours: Optional[float]

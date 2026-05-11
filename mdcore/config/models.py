from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class VaultConfig(BaseModel):
    path: str
    # Your own name as it appears in queries (e.g. "Piyush"). When present in a
    # query, the prefilter will deprioritise files from folders that belong to
    # other people (e.g. "Aishwarya Career/") so that "piyush career" returns
    # YOUR career notes, not someone else's.
    owner_name: str = ""
    excluded_folders: list[str] = Field(default_factory=lambda: ["noise"])
    excluded_extensions: list[str] = Field(default_factory=lambda: [".canvas"])
    # Multi-modal indexing: off by default. Requires pip install markdowncore-ai[multimodal]
    index_pdf: bool = False
    index_docx: bool = False
    index_txt: bool = False


class IndexerConfig(BaseModel):
    min_word_count: int = 50
    min_structure_signals: int = 1
    skip_structure_check_for: list[str] = Field(default_factory=lambda: [".pdf", ".docx", ".txt"])
    manifest_path: str = "~/.mdcore/manifest.json"
    chunk_size: int = 512
    chunk_overlap: int = 64
    max_chunk_words: int = 400
    heading_aware_splitting: bool = True
    preserve_tables: bool = True
    preserve_code_blocks: bool = True
    heading_levels: list[int] = Field(default_factory=lambda: [2, 3])
    batch_size: int = 32
    metadata_fields: list[str] = Field(default_factory=lambda: [
        "source_file", "folder_path", "filename", "heading_breadcrumb",
        "chunk_index", "chunk_total", "word_count", "is_table", "is_code", "last_indexed",
    ])


class EmbeddingsConfig(BaseModel):
    backend: Literal["ollama", "huggingface", "openai", "gemini"] = "ollama"
    local_model: str = "nomic-embed-text"   # ollama: nomic-embed-text / bge-m3; huggingface: all-MiniLM-L6-v2
    api_model: str = "text-embedding-3-small"
    api_key: Optional[str] = None
    cache_embeddings: bool = True
    cache_path: str = "~/.mdcore/embed_cache"


class VectorStoreConfig(BaseModel):
    backend: Literal["chroma"] = "chroma"
    persist_path: str = "~/.mdcore/chroma_db"
    collection_name: str = "mdcore_vault"
    distance_metric: Literal["cosine", "l2", "ip"] = "cosine"


class RetrieverConfig(BaseModel):
    keyword_prefilter: bool = True
    keyword_prefilter_min_score: float = 0.3
    top_k: int = 15
    similarity_threshold: float = 0.65

    context_block_max_words: int = 1000
    max_chunks_per_source: int = 2
    stitch_distance: int = 2
    stitch_max_words: int = 400

    signpost_max_items: int = 8
    signpost_include_section_hints: bool = True

    output_format: Literal["markdown", "plain"] = "markdown"
    include_word_count: bool = True
    include_timestamp: bool = True
    include_source_paths: bool = True
    include_similarity_scores: bool = False


class IngesterConfig(BaseModel):
    min_summary_word_count: int = 100
    min_summary_headings: int = 1
    similarity_threshold_high: float = 0.82
    similarity_threshold_low: float = 0.65
    max_candidates_for_llm: int = 3
    conflict_detection: bool = True
    conflict_similarity_min: float = 0.88
    conflict_similarity_max: float = 0.97
    folder_routing_confidence: float = 0.75


class FrontmatterConfig(BaseModel):
    inject: bool = True
    fields: list[str] = Field(default_factory=lambda: ["tags", "updated", "related"])
    tag_max_count: int = 8
    related_max_count: int = 5


class BackupConfig(BaseModel):
    enabled: bool = True
    backup_path: str = "~/.mdcore/backups"
    max_backups_per_file: int = 5


class WriterConfig(BaseModel):
    require_approval: bool = True
    append_position: Literal["end", "after_last_heading"] = "end"
    frontmatter: FrontmatterConfig = Field(default_factory=FrontmatterConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)


_LLMBackend = Literal["ollama", "openai", "anthropic", "gemini", "huggingface", "aggregator"]


class LLMConfig(BaseModel):
    # Primary backend — used for classify() and propose() during ingestion.
    backend: _LLMBackend = "ollama"
    model: str = "qwen3.5:4b"
    api_key: Optional[str] = None
    temperature: float = 0.2
    think: bool = False
    max_tokens: int = 1000
    timeout_seconds: int = 30
    fallback_backend: Optional[_LLMBackend] = None
    fallback_model: Optional[str] = None
    fallback_api_key: Optional[str] = None

    # Aggregator backend options (used when backend="aggregator").
    # Keys are managed by llm-keypool's SQLite DB — no api_key needed here.
    aggregator_category: Optional[str] = None  # target key pool (e.g. "general_purpose")
    aggregator_rotate_every: int = 5            # requests per key before forced rotation

    # Synthesis backend + model — used for synthesise() during mdcore search.
    # Defaults to primary backend + synthesise_model if not set.
    # Set synthesise_backend to use a completely different provider for synthesis.
    # Example: backend=ollama (ingestion) + synthesise_backend=aggregator (synthesis)
    synthesise_backend: Optional[_LLMBackend] = None
    synthesise_model: Optional[str] = None
    synthesise_api_key: Optional[str] = None

    # LangSmith observability (optional).
    # Set api_key + project to trace every LLM call at langsmith.com.
    langsmith_api_key: Optional[str] = None
    langsmith_project: Optional[str] = None


class ManifestConfig(BaseModel):
    path: str = "~/.mdcore/manifest.json"
    drift_warning_threshold: int = 3
    drift_warning_age_hours: int = 24


class CLIConfig(BaseModel):
    theme: Literal["dark", "light"] = "dark"
    confirm_before_index: bool = True
    show_similarity_scores: bool = False
    verbose: bool = False
    # output_folder is not configurable — always <vault.path>/mdcore-output/


class LoggingConfig(BaseModel):
    enabled: bool = True
    log_path: str = "~/.mdcore/logs"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_log_size_mb: int = 10
    max_log_files: int = 5


class MdCoreConfig(BaseModel):
    vault: VaultConfig
    indexer: IndexerConfig = Field(default_factory=IndexerConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    retriever: RetrieverConfig = Field(default_factory=RetrieverConfig)
    ingester: IngesterConfig = Field(default_factory=IngesterConfig)
    writer: WriterConfig = Field(default_factory=WriterConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    manifest: ManifestConfig = Field(default_factory=ManifestConfig)
    cli: CLIConfig = Field(default_factory=CLIConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

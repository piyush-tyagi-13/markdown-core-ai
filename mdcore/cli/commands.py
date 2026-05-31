from __future__ import annotations
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.syntax import Syntax
from rich.table import Table
from rich import box

from mdcore.config.loader import load_config, DEFAULT_CONFIG_PATH, DEFAULT_MODELS_PATH
from mdcore.config.models import MdCoreConfig

# ── mdcore init ───────────────────────────────────────────────────────────────

def _detect_ollama_models() -> list[str]:
    """Return list of pulled Ollama model tags, or [] if Ollama is unavailable."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        models = []
        for line in result.stdout.splitlines()[1:]:  # skip header
            tag = line.split()[0] if line.split() else ""
            if tag:
                models.append(tag)
        return models
    except Exception:
        return []


def _is_apple_silicon() -> bool:
    import platform
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _detect_ram_gb() -> int:
    """Best-effort RAM detection. Returns 0 if unavailable."""
    try:
        import platform
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3
            )
            return int(result.stdout.strip()) // (1024 ** 3)
        elif platform.system() == "Linux":
            import os
            page = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_PHYS_PAGES")
            return (page * pages) // (1024 ** 3)
    except Exception:
        pass
    return 0


def _hardware_label() -> str:
    import platform
    ram = _detect_ram_gb()
    ram_str = f"{ram}GB RAM" if ram else "unknown RAM"
    if _is_apple_silicon():
        return f"Apple Silicon · {ram_str}"
    return f"{platform.machine()} · {ram_str}"


# Model suggestion tables — pure hardcoded priority lists.
# No LLM call, no network call. Logic:
#   1. If user already has a preferred model pulled → suggest that.
#   2. Otherwise suggest based on detected RAM tier.
#   3. Fall back to qwen3.5:4b if nothing matches.
# RAM tiers: <8GB = small, 8-16GB = mid, >16GB = large.

_PRIMARY_MODELS_SMALL = ["phi4-mini", "qwen3.5:4b", "llama3.2:3b"]
_PRIMARY_MODELS_MID   = ["qwen3.5:4b", "qwen3:8b", "phi4-mini"]
_PRIMARY_MODELS_LARGE = ["qwen3:8b", "qwen3.5:4b", "llama3.1:8b"]


def _suggest_primary_model(pulled: list[str], ram_gb: int = 0) -> str:
    if ram_gb >= 24:
        preferred = _PRIMARY_MODELS_LARGE
    elif ram_gb >= 8:
        preferred = _PRIMARY_MODELS_MID
    else:
        preferred = _PRIMARY_MODELS_SMALL
    for m in preferred:
        if any(p.startswith(m.split(":")[0]) for p in pulled):
            return m
    # nothing pulled — suggest first in tier as the thing to pull
    return preferred[0]


def _suggest_synth_model(pulled: list[str]) -> str:
    # phi4-mini is always right here — non-thinking, fast, reliable
    # suggest regardless of whether it's pulled so user knows what to pull
    return "phi4-mini"


def _suggest_embed_model(pulled: list[str], ram_gb: int = 0) -> str:
    # bge-m3 is higher quality but heavier (~1.2GB vs ~0.5GB)
    # only suggest on machines with enough headroom
    if ram_gb >= 16 and not _is_apple_silicon():
        if any(p.startswith("bge-m3") for p in pulled):
            return "bge-m3"
    return "nomic-embed-text"


def _write_init_config(
    vault_path: str,
    owner_name: str,
    backend: str,
    primary_model: str,
    synth_model: str,
    embed_backend: str,
    embed_model: str,
    api_key: str,
    out_path: Path,
    aggregator_category: str = "",
    aggregator_rotate_every: int = 5,
    langsmith_key: str = "",
    langsmith_project: str = "",
) -> None:
    owner_line = f'  owner_name: "{owner_name}"' if owner_name else '  owner_name: ""  # set to your first name if vault has subfolders for other people'
    embed_model_field = "local_model" if embed_backend == "ollama" else "api_model"

    # synth_line — always written, never commented out (synthesis is a core flow)
    if backend == "aggregator":
        synth_line = "  # synthesise_model: null  # aggregator picks model from pool automatically"
        synth_line = ""  # synthesise_backend defaults to primary; aggregator handles it
    elif synth_model:
        synth_line = f"  synthesise_model: {synth_model}"
    else:
        # API backends: synthesis uses same backend + model by default; user can override
        synth_line = f"  synthesise_model: {primary_model}"

    # api_key / aggregator fields
    if backend == "aggregator":
        cat = aggregator_category or "general_purpose"
        api_key_line = f"  aggregator_category: {cat}\n  aggregator_rotate_every: {aggregator_rotate_every}"
    else:
        api_key_line = f"  api_key: {api_key}" if api_key else "  api_key: null"

    # embed api key
    embed_api_key_line = f"  api_key: {api_key}" if embed_backend != "ollama" and api_key else ""

    config_text = f"""# mdcore config — generated by mdcore init
# Edit any value, then run: mdcore config --validate

vault:
  path: {vault_path}
{owner_line}
  excluded_folders:
    - noise
  excluded_extensions:
    - .canvas
    - .pdf

indexer:
  min_word_count: 50
  chunk_size: 512
  chunk_overlap: 64
  heading_aware_splitting: true
  preserve_tables: true
  preserve_code_blocks: true

embeddings:
  backend: {embed_backend}
  {embed_model_field}: {embed_model}
{embed_api_key_line}
vector_store:
  persist_path: .mdcore-index/chroma_db
  collection_name: mdcore_vault

retriever:
  keyword_prefilter: true
  top_k: 15
  similarity_threshold: 0.65
  context_block_max_words: 1000

ingester:
  similarity_threshold_high: 0.82
  similarity_threshold_low: 0.65
  conflict_detection: true

writer:
  append_position: end
  backup:
    enabled: true

llm:
  backend: {backend}
{"  # model: null  # aggregator selects model from key pool" if backend == "aggregator" else f"  model: {primary_model}"}
{synth_line}
{api_key_line}
  temperature: 0.2
  think: false
  max_tokens: 1000
  timeout_seconds: 30
{"  langsmith_api_key: " + langsmith_key if langsmith_key else "  # langsmith_api_key: <your-key>   # uncomment to enable LangSmith tracing"}
{"  langsmith_project: " + (langsmith_project or "mdcore") if langsmith_key else "  # langsmith_project: mdcore"}

manifest:
  path: .mdcore-index/manifest.json
  drift_warning_threshold: 3

cli:
  theme: dark
  confirm_before_index: true

logging:
  log_level: INFO
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(config_text, encoding="utf-8")


try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("markdowncore-ai")
except Exception:
    _VERSION = "unknown"

app = typer.Typer(
    name="mdcore",
    help="Context Portability and Knowledge Ingestion Tool",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True, style="bold red")

_cfg_option = typer.Option(None, "--config", "-c", help="Path to config file")
_models_option = typer.Option(None, "--models", "-m", help="Path to models.yaml (overrides llm + embeddings sections)")


def _version_callback(value: bool) -> None:
    if value:
        print(f"mdcore {_VERSION}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", "-V",
        callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


def _load(config: Optional[str], models: Optional[str] = None) -> MdCoreConfig:
    cfg = load_config(config, models_path=models)
    from mdcore.utils.logging import setup_logging
    setup_logging(cfg.logging)
    return cfg


def _make_store(cfg: MdCoreConfig):
    from mdcore.store.vector_store import VectorStore
    return VectorStore(cfg.vector_store)


def _make_engine(cfg: MdCoreConfig):
    from mdcore.core.indexer.embedding_engine import EmbeddingEngine
    return EmbeddingEngine(cfg.embeddings)


# ── mdcore init ──────────────────────────────────────────────────────────────

@app.command()
def init(
    config: Optional[str] = _cfg_option,
):
    """Interactive setup wizard — create ~/.mdcore/config.yaml."""
    config_path = Path(config).expanduser() if config else DEFAULT_CONFIG_PATH

    console.print(Panel.fit(
        "[bold cyan]mdcore init[/bold cyan] — setup wizard\n"
        "[dim]Press Enter to accept a suggestion. Ctrl+C to abort.[/dim]",
        border_style="cyan",
    ))

    # ── existing config ───────────────────────────────────────────────────────
    if config_path.exists():
        overwrite = typer.confirm(f"\nConfig already exists at {config_path}. Overwrite?", default=False)
        if not overwrite:
            console.print("[yellow]Aborted — existing config kept.[/yellow]")
            raise typer.Exit()

    # ── detect hardware + Ollama ──────────────────────────────────────────────
    ram_gb = _detect_ram_gb()
    hw = _hardware_label()

    console.print("\n[dim]Detecting Ollama…[/dim]", end=" ")
    pulled_models = _detect_ollama_models()
    ollama_available = bool(pulled_models)
    if ollama_available:
        console.print(f"[green]found[/green]  ({len(pulled_models)} models pulled)")
        console.print(f"  [dim]{', '.join(pulled_models[:6])}{'…' if len(pulled_models) > 6 else ''}[/dim]")
    else:
        console.print("[yellow]not found[/yellow]  (Ollama not running or not installed)")

    console.print(f"[dim]Hardware: {hw}[/dim]\n")

    # ── vault path ────────────────────────────────────────────────────────────
    console.rule("[bold]Vault", align="left")
    console.print("[dim]Absolute path to your markdown folder (Obsidian vault or any folder of .md files)[/dim]")
    default_vault = str(Path("~/Documents").expanduser())
    vault_path = typer.prompt("Vault path", default=default_vault)
    vault_path = str(Path(vault_path).expanduser())
    if not Path(vault_path).exists():
        console.print(f"[yellow]Warning:[/yellow] {vault_path} does not exist. Create it before running mdcore index.")

    console.print("\n[dim]Owner name: your first name if the vault has subfolders for other people (e.g. a partner's notes).[/dim]")
    console.print("[dim]Queries like 'piyush career' will route to your files, not theirs. Leave blank for single-person vaults.[/dim]")
    owner_name = typer.prompt("Owner name", default="")

    # ── LLM backend ───────────────────────────────────────────────────────────
    console.rule("[bold]LLM backend", align="left")
    backend_options = {
        "1": ("ollama",      "local — no API cost, requires Ollama running"),
        "2": ("openai",      "OpenAI API (gpt-4o-mini recommended)"),
        "3": ("anthropic",   "Anthropic API (claude-haiku-4-5 recommended)"),
        "4": ("gemini",      "Google Gemini API"),
        "5": ("aggregator",  "llm-keypool — free-tier key pool, no single API key needed"),
    }
    for k, (name, desc) in backend_options.items():
        tag = " [green][suggested][/green]" if (k == "1" and ollama_available) or (k == "2" and not ollama_available) else ""
        console.print(f"  [{k}] {name} — {desc}{tag}")

    default_backend_key = "1" if ollama_available else "2"
    backend_key = typer.prompt("Backend", default=default_backend_key)
    backend = backend_options.get(backend_key, backend_options[default_backend_key])[0]

    api_key = ""
    if backend not in ("ollama", "aggregator"):
        api_key = typer.prompt(f"{backend} API key", default="", hide_input=True)

    # ── models ────────────────────────────────────────────────────────────────
    console.rule("[bold]Models", align="left")

    aggregator_category = ""
    aggregator_rotate_every = 5

    synth_backend = backend   # default: synthesis uses same backend as primary

    if backend == "aggregator":
        console.print("\n[dim]aggregator uses a local SQLite key pool — no single API key needed.[/dim]")
        console.print("[dim]Model selection is automatic based on registered keys.[/dim]")
        aggregator_category = typer.prompt("Key pool category", default="general_purpose")
        aggregator_rotate_every = int(typer.prompt("Rotate every N requests", default="5"))
        primary_model = ""
        synth_model = ""
        console.print("\n[dim]Embedding backend — separate from LLM backend.[/dim]")
        console.print("  [dim]Options: ollama, gemini, openai[/dim]")
        embed_backend = typer.prompt("Embedding backend", default="ollama")
        embed_api_defaults = {
            "openai": "text-embedding-3-small",
            "gemini": "models/gemini-embedding-001",
            "ollama": "nomic-embed-text",
        }
        embed_model = typer.prompt("Embedding model", default=embed_api_defaults.get(embed_backend, "nomic-embed-text"))
        if embed_backend not in ("ollama",):
            embed_api_key_for_agg = typer.prompt(f"{embed_backend} API key for embeddings", default="", hide_input=True)
            api_key = embed_api_key_for_agg
        from pathlib import Path as _Path
        _agg_db = _Path.home() / ".llm-keypool" / "keys.db"
        console.rule("[bold cyan]Register API keys with llm-keypool", align="left")
        console.print("[bold]Suggested free-tier providers and models:[/bold]")
        console.print("  [cyan]Groq[/cyan]       llm-keypool add --provider groq --key <KEY> --model llama-3.3-70b-versatile --category general_purpose")
        console.print("  [cyan]Cerebras[/cyan]   llm-keypool add --provider cerebras --key <KEY> --model llama-3.3-70b --category general_purpose")
        console.print("  [cyan]Mistral[/cyan]    llm-keypool add --provider mistral --key <KEY> --model mistral-small-latest --category general_purpose")
        console.print("  [cyan]OpenRouter[/cyan] llm-keypool add --provider openrouter --key <KEY> --model meta-llama/llama-3.3-70b-instruct:free --category general_purpose")
        console.print("\n  Get keys (all free tier):")
        console.print("  [dim]Groq:[/dim]       https://console.groq.com/keys")
        console.print("  [dim]Cerebras:[/dim]   https://cloud.cerebras.ai")
        console.print("  [dim]Mistral:[/dim]    https://console.mistral.ai/api-keys")
        console.print("  [dim]OpenRouter:[/dim] https://openrouter.ai/settings/keys")
        console.print(f"\n[bold]Config file locations:[/bold]")
        console.print(f"  mdcore config:         [cyan]{config_path}[/cyan]")
        console.print(f"  llm-keypool keys DB: [cyan]{_agg_db}[/cyan]  (managed via llm-keypool CLI)")
        console.print(f"  View registered keys:   [dim]llm-keypool status[/dim]")
        register_now = typer.confirm("\nRegister keys now? (open a new terminal tab, run the commands above, come back)", default=False)
        if register_now:
            typer.prompt("Press Enter when done registering keys", default="")
        else:
            console.print("[dim]Skipped. Register keys any time with the commands above before running mdcore.[/dim]")

    elif backend == "ollama":
        suggested_primary = _suggest_primary_model(pulled_models, ram_gb)
        suggested_synth   = _suggest_synth_model(pulled_models)
        suggested_embed   = _suggest_embed_model(pulled_models, ram_gb)

        console.print("[dim]Primary model: classification + proposals during ingest. Must be instruction-following.[/dim]")
        if pulled_models:
            console.print(f"  [dim]Pulled: {', '.join(pulled_models[:8])}[/dim]")
        console.print(f"  [dim]Suggested for {hw}: {suggested_primary}[/dim]")
        primary_model = typer.prompt("Primary model", default=suggested_primary)

        console.print(f"\n[dim]Synthesis model: rewrites raw excerpts into a briefing during mdcore search.[/dim]")
        console.print(f"  [dim]Can be a different backend (e.g. OpenAI) — set synthesise_backend in config.[/dim]")
        console.print(f"  [dim]For Ollama: phi4-mini is ideal (non-thinking, fast, ~2.5GB). Suggested: {suggested_synth}[/dim]")
        if not any(p.startswith("phi4-mini") for p in pulled_models):
            console.print(f"  [yellow]phi4-mini not pulled — run: ollama pull phi4-mini[/yellow]")
        synth_model = typer.prompt("Synthesis model", default=suggested_synth)

        console.print(f"\n[dim]Embedding model: generates vectors for indexing and search queries.[/dim]")
        console.print(f"  [dim]Suggested for {hw}: {suggested_embed}[/dim]")
        if not any(p.startswith(suggested_embed.split(":")[0]) for p in pulled_models):
            console.print(f"  [yellow]{suggested_embed} not pulled — run: ollama pull {suggested_embed}[/yellow]")
        embed_backend = "ollama"
        embed_model = typer.prompt("Embedding model", default=suggested_embed)

    else:
        api_defaults = {
            "openai":    ("gpt-4o-mini",       "text-embedding-3-small"),
            "anthropic": ("claude-haiku-4-5",   "text-embedding-3-small"),
            "gemini":    ("gemini-2.0-flash",   "models/text-embedding-004"),
        }
        def_primary, def_embed = api_defaults.get(backend, ("gpt-4o-mini", "text-embedding-3-small"))
        primary_model = typer.prompt("Primary model", default=def_primary)
        console.print(f"\n[dim]Synthesis model: rewrites raw excerpts into a briefing during mdcore search.[/dim]")
        console.print(f"  [dim]Defaults to primary model. Can be a cheaper/faster model.[/dim]")
        synth_model = typer.prompt("Synthesis model", default=primary_model)
        console.print("\n[dim]Embedding backend (can differ from LLM backend)[/dim]")
        console.print("  [dim]Options: ollama, openai, huggingface, gemini[/dim]")
        embed_backend = typer.prompt("Embedding backend", default=backend)
        embed_model   = typer.prompt("Embedding model", default=def_embed)

    # ── LangSmith tracing (optional) ──────────────────────────────────────────
    console.rule("[bold]Observability (optional)", align="left")
    console.print("[dim]LangSmith traces every LLM call — token usage, latency, prompts.[/dim]")
    console.print("[dim]Free tier available at https://smith.langchain.com[/dim]")
    langsmith_key = ""
    langsmith_project = ""
    if typer.confirm("Enable LangSmith tracing?", default=False):
        langsmith_key = typer.prompt("LangSmith API key", hide_input=True)
        langsmith_project = typer.prompt("LangSmith project name", default="mdcore")

    # ── write ─────────────────────────────────────────────────────────────────
    _write_init_config(
        vault_path=vault_path,
        owner_name=owner_name,
        backend=backend,
        primary_model=primary_model,
        synth_model=synth_model,
        embed_backend=embed_backend,
        embed_model=embed_model,
        api_key=api_key,
        out_path=config_path,
        aggregator_category=aggregator_category,
        aggregator_rotate_every=aggregator_rotate_every,
        langsmith_key=langsmith_key,
        langsmith_project=langsmith_project,
    )

    console.print(f"\n[green]✓[/green] Config written → [cyan]{config_path}[/cyan]")

    # ── offer to install missing deps ─────────────────────────────────────────
    try:
        cfg_written = _load(str(config_path))
        from mdcore.core.deps import required_backends, install_packages
        statuses = required_backends(cfg_written)
        missing_deps = [s for s in statuses if not s.installed and s.packages]
        if missing_deps:
            all_pkgs: list[str] = []
            console.print("\n[yellow]Missing backend packages:[/yellow]")
            for s in missing_deps:
                console.print(f"  {s.role}/{s.backend}: {', '.join(s.packages)}")
                all_pkgs.extend(s.packages)
            seen_pkgs: set[str] = set()
            deduped_pkgs = [p for p in all_pkgs if not (p in seen_pkgs or seen_pkgs.add(p))]  # type: ignore[func-returns-value]
            if typer.confirm("\nInstall them now?", default=True):
                with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as p:
                    p.add_task("Installing packages…")
                    ok = install_packages(deduped_pkgs, quiet=True)
                if ok:
                    console.print("[green]✓ Packages installed.[/green]")
                else:
                    console.print("[yellow]Install failed. Run: mdcore deps install[/yellow]")
    except Exception:
        pass  # dep check is best-effort — don't crash init

    # ── next steps ────────────────────────────────────────────────────────────
    console.print("\n[bold]Next steps:[/bold]")
    if backend == "ollama":
        missing_models = [
            m for m in {primary_model, synth_model, embed_model}
            if m and not any(p.startswith(m.split(":")[0]) for p in pulled_models)
        ]
        if missing_models:
            console.print("  [yellow]Pull missing Ollama models:[/yellow]")
            for m in missing_models:
                console.print(f"    ollama pull {m}")
    console.print("  [cyan]mdcore deps status[/cyan]        — verify all backend deps installed")
    console.print("  [cyan]mdcore docs config[/cyan]        — full config guide (all fields + tuning)")
    console.print("  [cyan]mdcore config --validate[/cyan]  — verify config is valid")
    console.print("  [cyan]mdcore index[/cyan]              — index your vault")


# ── mdcore index ─────────────────────────────────────────────────────────────

@app.command()
def index(
    config: Optional[str] = _cfg_option,
    models: Optional[str] = _models_option,
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    inspect: Optional[str] = typer.Option(None, "--inspect", help="Show chunk breakdown for a file"),
    force: bool = typer.Option(False, "--force", "-f", help="Wipe index and manifest, reindex everything from scratch"),
):
    """Scan vault, show diff, confirm, index delta."""
    cfg = _load(config, models)

    from mdcore.core.indexer.vault_scanner import VaultScanner
    from mdcore.core.indexer.manifest_manager import ManifestManager
    from mdcore.core.indexer.document_loader import DocumentLoader
    from mdcore.core.indexer.multimodal_loader import MultiModalLoader
    from mdcore.core.indexer.text_splitter import TextSplitter
    from mdcore.core.indexer.index_writer import IndexWriter
    from mdcore.utils.logging import get_logger
    from mdcore.config.loader import expand_path

    if force:
        import shutil
        manifest_path = expand_path(cfg.manifest.path)
        chroma_path = Path(cfg.vector_store.persist_path).expanduser()
        cache_path = expand_path(cfg.embeddings.cache_path) / "embed_cache.pkl"
        if manifest_path.exists():
            manifest_path.unlink()
            console.print(f"[dim]Deleted manifest: {manifest_path}[/dim]")
        if chroma_path.exists():
            shutil.rmtree(chroma_path)
            console.print(f"[dim]Deleted vector store: {chroma_path}[/dim]")
        if cache_path.exists():
            cache_path.unlink()
            console.print(f"[dim]Deleted embed cache: {cache_path}[/dim]")
        console.print("[yellow]Force reindex — all files will be re-indexed.[/yellow]")

    scanner = VaultScanner(cfg.vault, cfg.indexer)
    manifest = ManifestManager(cfg.manifest, cfg.vault)
    loader = DocumentLoader(cfg.vault)
    mm_loader = MultiModalLoader(cfg.vault)
    splitter = TextSplitter(cfg.indexer)
    store = _make_store(cfg)
    engine = _make_engine(cfg)
    writer = IndexWriter(store, engine, cfg.indexer)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        task = p.add_task("Scanning vault...")
        eligible = scanner.scan()
        p.update(task, description=f"Found {len(eligible)} eligible files")

    # ── vault map: nudge if .mdcore-meta.yaml doesn't exist yet ──────────
    from mdcore.core.vault_map import VaultMap
    vault_path = Path(cfg.vault.path).expanduser()
    _meta_file = vault_path / ".mdcore-meta.yaml"
    if not _meta_file.exists():
        console.print(
            "[dim]Tip: run [bold]mdcore map[/bold] to describe your vault folders "
            "— improves doc routing during ingest.[/dim]"
        )

    diff = manifest.diff(eligible)

    if inspect:
        _show_inspect(inspect, loader, splitter, cfg.vault)
        return

    if diff.total_changes == 0:
        console.print("[green]Index is up to date — nothing to do.[/green]")
        return

    # Summary panel
    summary = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    summary.add_column("Label", style="dim")
    summary.add_column("Count", justify="right")
    summary.add_row("Total eligible", str(len(eligible)))
    summary.add_row("─" * 20, "─" * 6)
    summary.add_row("[green]New (unindexed)[/green]", f"[green]{len(diff.new_files)}[/green]")
    summary.add_row("[yellow]Modified (stale)[/yellow]", f"[yellow]{len(diff.modified_files)}[/yellow]")
    summary.add_row("[red]Deleted[/red]", f"[red]{len(diff.deleted_files)}[/red]")
    summary.add_row("─" * 20, "─" * 6)
    summary.add_row("[bold]Delta total[/bold]", f"[bold]{diff.total_changes}[/bold]")
    console.print(Panel(summary, title="[bold cyan]Vault Scan Summary[/bold cyan]", expand=False))

    # Delta table
    table = Table(
        title=f"Index Delta  ({diff.total_changes} files)",
        box=box.ROUNDED,
        caption=f"{len(diff.new_files)} new · {len(diff.modified_files)} modified · {len(diff.deleted_files)} deleted",
    )
    table.add_column("Status", style="cyan")
    table.add_column("File")
    for p in diff.new_files:
        table.add_row("[+] New", str(p.relative_to(Path(cfg.vault.path).expanduser())))
    for p in diff.modified_files:
        table.add_row("[~] Modified", str(p.relative_to(Path(cfg.vault.path).expanduser())))
    for k in diff.deleted_files:
        table.add_row("[-] Deleted", k)
    console.print(table)

    if cfg.cli.confirm_before_index:
        choice = typer.prompt("[A]ll / [C]ancel", default="A").strip().upper()
        if choice != "A":
            console.print("Cancelled.")
            return

    files_to_index = diff.new_files + diff.modified_files
    start = datetime.now(timezone.utc)
    skipped: list[tuple[Path, str]] = []

    _log = get_logger("cli.index")

    with Progress(SpinnerColumn(), BarColumn(), TextColumn("{task.description}"), console=console) as p:
        task = p.add_task("Indexing...", total=len(files_to_index))
        for path in files_to_index:
            try:
                doc = loader.load(path) if path.suffix.lower() == ".md" else mm_loader.load(path)
                chunks = splitter.split(doc)
                source_file = doc.metadata.get("source_file", str(path))
                writer.write(chunks, source_file)
                manifest.update(path)
                if verbose:
                    console.print(f"  [dim]Indexed {source_file} → {len(chunks)} chunks[/dim]")
            except Exception as exc:
                _log.exception("Failed to index %s", path)
                skipped.append((path, str(exc)))
                console.print(f"  [yellow]⚠ Skipped:[/yellow] {path.name} — {exc}")
            finally:
                p.advance(task)

    for key in diff.deleted_files:
        store.delete(key)
        manifest.remove(key)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    indexed_count = len(files_to_index) - len(skipped)
    summary = (
        f"[green]Done.[/green] {indexed_count} indexed, "
        f"{len(diff.deleted_files)} removed in {elapsed:.1f}s"
    )
    if skipped:
        summary += f" [yellow]· {len(skipped)} skipped (see warnings above)[/yellow]"
    console.print(summary)


def _show_inspect(filename: str, loader, splitter, vault_cfg):
    from pathlib import Path
    vault = Path(vault_cfg.path).expanduser()
    matches = list(vault.rglob(f"*{filename}*"))
    if not matches:
        console.print(f"[red]No file matching '{filename}' found.[/red]")
        return
    path = matches[0]
    doc = loader.load(path)
    chunks = splitter.split(doc)
    table = Table(title=f"Chunks: {path.name}", box=box.SIMPLE)
    table.add_column("#", style="dim")
    table.add_column("Breadcrumb")
    table.add_column("Words")
    table.add_column("Table")
    table.add_column("Code")
    for c in chunks:
        m = c.metadata
        table.add_row(
            str(m.get("chunk_index", "?")),
            m.get("heading_breadcrumb", "—"),
            str(m.get("word_count", "?")),
            "✓" if m.get("is_table") else "",
            "✓" if m.get("is_code") else "",
        )
    console.print(table)


# ── mdcore search ────────────────────────────────────────────────────────────

def _query_slug(topic: str) -> str:
    """Convert query to a safe filename slug: lowercase, hyphens, max 60 chars."""
    import re
    slug = re.sub(r"[^\w\s-]", "", topic.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60]


@app.command()
def search(
    topic: str = typer.Argument(..., help="Topic to search for"),
    config: Optional[str] = _cfg_option,
    models: Optional[str] = _models_option,
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    raw: bool = typer.Option(
        False, "--raw", "-r",
        help="Write raw retrieved excerpts instead of synthesised briefing.",
    ),
):
    """Flow A — retrieve context, synthesise with local LLM, write to MD file."""
    cfg = _load(config, models)

    from mdcore.core.retriever.keyword_prefilter import KeywordPreFilter
    from mdcore.core.retriever.vector_searcher import VectorSearcher
    from mdcore.core.retriever.chunk_grouper import group_by_source
    from mdcore.core.retriever.chunk_stitcher import stitch
    from mdcore.core.retriever.source_ranker import rank_sources
    from mdcore.core.retriever.context_assembler import assemble
    from mdcore.core.retriever.context_formatter import format_context, raw_text_for_synthesis

    store = _make_store(cfg)
    engine = _make_engine(cfg)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as p:
        p.add_task("Searching...")

        all_chunks = store.all_chunks()
        if not all_chunks:
            console.print("[yellow]Index is empty. Run [bold]mdcore index[/bold] first.[/yellow]")
            return

        candidate_sources: set[str] | None = None
        if cfg.retriever.keyword_prefilter:
            prefilter = KeywordPreFilter(
                cfg.retriever.keyword_prefilter_min_score,
                owner_name=cfg.vault.owner_name,
                mode=cfg.retriever.keyword_prefilter_mode,
            )
            candidate_sources = prefilter.filter(topic, all_chunks) or None

        # Strip owner name from vector query — not in content, adds noise.
        vector_query = topic
        if cfg.vault.owner_name:
            owner_words_lc = {w.lower() for w in cfg.vault.owner_name.split()}
            stripped = " ".join(w for w in topic.split() if w.lower() not in owner_words_lc)
            if stripped.strip():
                vector_query = stripped.strip()

        searcher = VectorSearcher(store, engine, cfg.retriever)
        chunks = searcher.search(vector_query, candidate_sources)

    if not chunks:
        console.print(f"[yellow]No results found for '{topic}'. Try lowering similarity_threshold in config.[/yellow]")
        return

    if verbose:
        console.print(f"[dim]Raw chunks: {len(chunks)}[/dim]")

    groups = group_by_source(chunks)
    passages_by_source = {sf: stitch(sf, c, cfg.retriever) for sf, c in groups.items()}
    ranked = rank_sources(passages_by_source)
    ctx = assemble(topic, ranked, cfg.retriever)

    # ── Build output content ──────────────────────────────────────────────────
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    source_lines = "\n".join(
        f"- [{i}] {sf}" for i, (sf, _) in enumerate(ctx.primary, 1)
    )

    if raw:
        raw_output = format_context(ctx, cfg.retriever)
        md_content = (
            f"# {topic}\n\n"
            f"*{now_str} · {ctx.source_count} sources · raw excerpts*\n\n"
            f"## Sources\n\n{source_lines}\n\n"
            "---\n\n"
            f"{raw_output}"
        )
        mode_label = "raw"
    else:
        from mdcore.llm.llm_layer import LLMLayer
        llm = LLMLayer(cfg.llm)
        raw_text = raw_text_for_synthesis(ctx)
        synth_model_name = cfg.llm.synthesise_model or cfg.llm.model

        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as p:
            p.add_task(f"Synthesising with {synth_model_name}…")
            briefing = llm.synthesise(topic, raw_text)

        raw_output = format_context(ctx, cfg.retriever)
        md_content = (
            f"# {topic}\n\n"
            f"*{now_str} · {ctx.source_count} sources · synthesised by {synth_model_name}*\n\n"
            f"> ⚠ Verify claims against raw excerpts below.\n\n"
            f"## Sources\n\n{source_lines}\n\n"
            "---\n\n"
            f"## Briefing\n\n{briefing}\n\n"
            "---\n\n"
            f"## Raw Excerpts\n\n{raw_output}"
        )
        mode_label = f"synthesised · {synth_model_name}"

    # ── Write to output folder ────────────────────────────────────────────────
    # Output lives at <vault>/mdcore-output/ — fixed location, always excluded from indexing.
    out_dir = Path(cfg.vault.path).expanduser() / "mdcore-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"{date_prefix}-{_query_slug(topic)}.md"
    out_path = out_dir / filename

    out_path.write_text(md_content, encoding="utf-8")
    console.print(f"[green]✓[/green] Saved → [cyan]{out_path}[/cyan]  [dim]({ctx.source_count} sources · {mode_label})[/dim]")


# ── mdcore ingest ─────────────────────────────────────────────────────────────

@app.command()
def ingest(
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Path to summary file"),
    config: Optional[str] = _cfg_option,
    models: Optional[str] = _models_option,
):
    """Flow B — accept session summary, classify, propose."""
    cfg = _load(config, models)

    from mdcore.core.ingester.summary_receiver import SummaryReceiver
    from mdcore.core.ingester.summary_embedder import SummaryEmbedder
    from mdcore.core.ingester.classification_engine import ClassificationEngine
    from mdcore.core.ingester.folder_router import FolderRouter
    from mdcore.core.ingester.conflict_detector import ConflictDetector
    from mdcore.core.ingester.proposal_generator import ProposalGenerator
    from mdcore.llm.llm_layer import LLMLayer

    receiver = SummaryReceiver(cfg.ingester)
    store = _make_store(cfg)
    engine = _make_engine(cfg)
    llm = LLMLayer(cfg.llm)

    try:
        if file:
            summary = receiver.receive_from_file(file)
        else:
            console.print("[bold]Paste your session summary below. Press [Ctrl+D] when done:[/bold]")
            lines = sys.stdin.read()
            summary = receiver.receive_from_text(lines)
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"Input error: {exc}")
        raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as p:
        p.add_task("Classifying...")
        from mdcore.core.ingester.summary_embedder import SummaryEmbedder
        embedder = SummaryEmbedder(engine)
        embs = embedder.embed(summary)
        classify_engine = ClassificationEngine(store, llm, cfg.ingester)
        decision = classify_engine.classify(embs.full, summary)

    console.print(f"\nAction: [bold]{decision.action.upper()}[/bold]")
    if decision.target_file:
        console.print(f"Target: [cyan]{decision.target_file}[/cyan]")
    console.print(f"Confidence: {decision.confidence:.2f}")
    console.print(f"Reasoning: [dim]{decision.reasoning}[/dim]")
    if decision.used_llm:
        console.print("[dim](LLM was consulted for ambiguous classification)[/dim]")

    existing_content = ""
    conflicts = []
    if decision.action == "update" and decision.target_file:
        vault_path = Path(cfg.vault.path).expanduser()
        existing_path = vault_path / decision.target_file
        if existing_path.exists():
            existing_content = existing_path.read_text(encoding="utf-8", errors="ignore")
            detector = ConflictDetector(engine, cfg.ingester)
            conflicts = detector.detect(existing_content, summary)

    folder = ""
    if decision.action == "new":
        router = FolderRouter(cfg.vault, cfg.ingester, llm)
        folder, folder_confidence = router.route(summary, top_scores=decision.top_scores)
        if router.needs_confirmation(folder_confidence):
            console.print(f"\n[yellow]Suggested folder: '{folder}' (low confidence)[/yellow]")
            override = typer.prompt("Enter folder path or press Enter to accept", default=folder)
            folder = override.strip()
        else:
            console.print(f"Suggested folder: [cyan]{folder}[/cyan]")

    from datetime import date
    fm_updates = {"updated": str(date.today()), "tags": [], "related": []}

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as p:
        p.add_task("Generating proposal...")
        generator = ProposalGenerator(llm)
        proposal = generator.generate(decision, summary, existing_content, conflicts, folder, fm_updates)

    _render_proposal(proposal)

    choice = typer.prompt("\n[A]pprove / [E]dit first / [R]eject", default="R").strip().upper()
    if choice == "R":
        console.print("[dim]Rejected. No changes made.[/dim]")
        return
    if choice == "E":
        console.print("[yellow]Edit mode: open your editor, save the summary, then re-run mdcore ingest --file <path>[/yellow]")
        return

    _execute_write(cfg, proposal, summary, existing_content)


def _render_proposal(proposal):
    from mdcore.core.ingester.proposal_generator import Proposal
    lines = [
        "PROPOSAL -- mdcore ingestion",
        "─" * 45,
        f"Action:     {proposal.action.upper()} {'existing file' if proposal.action == 'update' else 'new file'}",
    ]
    if proposal.target_file:
        lines.append(f"Target:     {proposal.target_file}")
    elif proposal.suggested_folder:
        lines.append(f"Folder:     {proposal.suggested_folder}")
    lines.append(f"Confidence: {proposal.confidence:.2f}")
    lines.append("")
    lines.append("Changes proposed:")
    for line in proposal.proposal_text.strip().splitlines():
        lines.append(f"  {line}")
    if proposal.conflicts:
        lines.append("")
        lines.append("Possible conflicts detected:")
        for c in proposal.conflicts[:3]:
            lines.append(f"  ! Existing:  \"{c.existing_sentence[:80]}\"")
            lines.append(f"    Incoming:  \"{c.incoming_sentence[:80]}\"")
            lines.append(f"    → Review before approving (similarity {c.similarity:.2f})")
    lines.append("─" * 45)
    console.print(Panel("\n".join(lines), title="[bold]mdcore proposal[/bold]", expand=False))


def _execute_write(cfg: MdCoreConfig, proposal, summary: str, existing_content: str):
    from mdcore.core.writer.backup_manager import BackupManager
    from mdcore.core.writer.frontmatter_injector import FrontmatterInjector
    from mdcore.core.writer.file_writer import FileWriter
    from mdcore.core.writer.index_trigger import IndexTrigger
    from mdcore.core.indexer.manifest_manager import ManifestManager
    from mdcore.core.indexer.document_loader import DocumentLoader
    from mdcore.core.indexer.text_splitter import TextSplitter
    from mdcore.core.indexer.index_writer import IndexWriter

    vault_path = Path(cfg.vault.path).expanduser()
    backup_mgr = BackupManager(cfg.writer.backup)
    fm_injector = FrontmatterInjector(cfg.writer.frontmatter)
    file_writer = FileWriter(cfg.vault, cfg.writer)
    store = _make_store(cfg)
    engine = _make_engine(cfg)

    def _factory():
        loader = DocumentLoader(cfg.vault)
        splitter = TextSplitter(cfg.indexer)
        idx_writer = IndexWriter(store, engine, cfg.indexer)
        manifest = ManifestManager(cfg.manifest, cfg.vault)
        return loader, splitter, idx_writer, manifest

    trigger = IndexTrigger(_factory)

    if proposal.action == "update" and proposal.target_file:
        target = vault_path / proposal.target_file
        backup_mgr.backup(target)
        updated_fm = fm_injector.inject(target, proposal.frontmatter_updates)
        file_writer.update(target, updated_fm, summary)
        trigger.reindex(target)
        console.print(f"[green]Updated:[/green] {proposal.target_file}")
    else:
        filename = _derive_filename(summary)
        new_path = file_writer.create(proposal.suggested_folder, filename, summary)
        trigger.reindex(new_path)
        console.print(f"[green]Created:[/green] {new_path}")


def _derive_filename(summary: str) -> str:
    import re
    for line in summary.splitlines():
        m = re.match(r"^#{1,3}\s+(.+)$", line)
        if m:
            title = m.group(1).strip()
            safe = re.sub(r"[^a-z0-9\- ]", "", title.lower())
            return safe.replace(" ", "-")[:50] + ".md"
    return "ingested-note.md"


# ── mdcore map ────────────────────────────────────────────────────────────────

@app.command(name="map")
def vault_map_cmd(
    config: Optional[str] = _cfg_option,
    models: Optional[str] = _models_option,
    repair: bool = typer.Option(False, "--repair", help="Remove descriptions for folders that no longer exist"),
):
    """Generate vault folder map file — open it, add descriptions, then run mdcore index."""
    cfg = _load(config, models)
    from mdcore.core.vault_map import VaultMap
    vault_path = Path(cfg.vault.path).expanduser()
    vault_map = VaultMap(vault_path)

    if repair:
        stale = vault_map.stale_descriptions()
        if stale:
            for f in stale:
                vault_map.remove_description(f)
            vault_map.save()
            console.print(f"[green]Removed {len(stale)} stale description(s).[/green]")
        else:
            console.print("[dim]No stale descriptions found.[/dim]")
        return

    all_folders = vault_map.all_vault_folders()
    if not all_folders:
        console.print("[yellow]No subfolders found in vault.[/yellow]")
        return

    meta_file = vault_map.write_template()
    console.print(f"[green]✓[/green] Vault map written: [cyan]{meta_file}[/cyan]")
    console.print(f"  [dim]{len(all_folders)} folders listed.[/dim]")
    console.print("\n  Open the file in your editor, add a short description")
    console.print("  next to each folder, save, then run [bold]mdcore index[/bold].")


# ── mdcore status ─────────────────────────────────────────────────────────────

@app.command()
def status(config: Optional[str] = _cfg_option, models: Optional[str] = _models_option):
    """Show index health, drift warnings, last sync."""
    cfg = _load(config, models)

    from mdcore.core.indexer.vault_scanner import VaultScanner
    from mdcore.core.indexer.manifest_manager import ManifestManager

    scanner = VaultScanner(cfg.vault, cfg.indexer)
    manifest = ManifestManager(cfg.manifest, cfg.vault)
    store = _make_store(cfg)

    eligible = scanner.scan()
    diff = manifest.diff(eligible)
    all_meta = store.all_metadata()
    indexed_files = len({m.get("source_file") for m in all_meta})
    total_chunks = len(all_meta)

    table = Table(title="mdcore status", box=box.ROUNDED)
    table.add_column("Item", style="cyan")
    table.add_column("Value")
    table.add_row("Vault path", cfg.vault.path)
    table.add_row("Eligible files", str(len(eligible)))
    table.add_row("Indexed files", str(indexed_files))
    table.add_row("Total chunks", str(total_chunks))
    table.add_row("New (unindexed)", str(len(diff.new_files)))
    table.add_row("Modified (stale)", str(len(diff.modified_files)))
    table.add_row("Deleted", str(len(diff.deleted_files)))
    console.print(table)

    if diff.total_changes >= cfg.manifest.drift_warning_threshold:
        console.print(
            f"[yellow]⚠ Index drift detected: {diff.total_changes} file(s) out of sync. "
            f"Run [bold]mdcore index[/bold] to update.[/yellow]"
        )
    else:
        console.print("[green]Index is healthy.[/green]")

    # ── llm-keypool active key (aggregator backend only) ─────────────────────
    if cfg.llm.backend == "aggregator":
        try:
            from llm_keypool import AggregatorChat
            chat = AggregatorChat(
                category=cfg.llm.aggregator_category or "general_purpose",
                rotate_every=cfg.llm.aggregator_rotate_every,
            )
            k = chat.current_key()
            if k:
                kt = Table(title="llm-keypool active key", box=box.SIMPLE_HEAVY)
                kt.add_column("Provider",  style="cyan")
                kt.add_column("Model")
                kt.add_column("Slot", justify="right")
                kt.add_column("Req today", justify="right")
                kt.add_column("Tok today", justify="right")
                kt.add_column("Cooldown")
                cd = k.get("cooldown_until")
                cd_str = f"[yellow]{cd[:19]}[/yellow]" if cd else "[green]none[/green]"
                kt.add_row(
                    k["provider"],
                    k["model"],
                    f"{k['cycle_position']}/{k['rotate_every']}",
                    str(k["requests_today"]),
                    str(k["tokens_used_today"]),
                    cd_str,
                )
                console.print(kt)
            else:
                console.print("[dim]llm-keypool: no keys available for category "
                              f"'{cfg.llm.aggregator_category or 'general_purpose'}'[/dim]")
        except Exception as e:
            console.print(f"[dim]llm-keypool active key unavailable: {e}[/dim]")


# ── mdcore eval ───────────────────────────────────────────────────────────────

@app.command()
def eval(
    topic: Optional[str] = typer.Argument(None, help="Topic to evaluate (runs a search if provided)"),
    config: Optional[str] = _cfg_option,
    models: Optional[str] = _models_option,
):
    """Run user-guided quality check on retrieval output."""
    cfg = _load(config, models)

    if topic:
        console.print(f"[bold]Running search for evaluation: '{topic}'[/bold]\n")
        # Invoke search inline
        from mdcore.core.retriever.keyword_prefilter import KeywordPreFilter
        from mdcore.core.retriever.vector_searcher import VectorSearcher
        from mdcore.core.retriever.chunk_grouper import group_by_source
        from mdcore.core.retriever.chunk_stitcher import stitch
        from mdcore.core.retriever.source_ranker import rank_sources
        from mdcore.core.retriever.context_assembler import assemble
        from mdcore.core.retriever.context_formatter import format_context

        store = _make_store(cfg)
        engine = _make_engine(cfg)
        all_chunks = store.all_chunks()

        candidate_sources = None
        if cfg.retriever.keyword_prefilter:
            prefilter = KeywordPreFilter(
                cfg.retriever.keyword_prefilter_min_score,
                owner_name=cfg.vault.owner_name,
                mode=cfg.retriever.keyword_prefilter_mode,
            )
            candidate_sources = prefilter.filter(topic, all_chunks) or None

        searcher = VectorSearcher(store, engine, cfg.retriever)
        chunks = searcher.search(topic, candidate_sources)
        if not chunks:
            console.print("[yellow]No results found.[/yellow]")
            return

        groups = group_by_source(chunks)
        passages_by_source = {sf: stitch(sf, c, cfg.retriever) for sf, c in groups.items()}
        ranked = rank_sources(passages_by_source)
        ctx = assemble(topic, ranked, cfg.retriever)
        output = format_context(ctx, cfg.retriever)
        console.print(Markdown(output))
        console.rule()

    console.print(Panel(
        "[bold]Evaluation checklist[/bold]\n\n"
        "1. Is word count close to target (1000 words)?\n"
        "2. Are the right sources cited?\n"
        "3. Is content within each passage coherent?\n"
        "4. Would this context block give a subscription LLM enough to work with?\n\n"
        "See [bold]mdcore-retrieval-and-eval-guide.md[/bold] for the symptom-to-fix guide.",
        title="[cyan]mdcore eval[/cyan]",
        expand=False,
    ))


# ── mdcore deps ───────────────────────────────────────────────────────────────

@app.command()
def deps(
    action: str = typer.Argument("status", help="Action: status | install"),
    config: Optional[str] = _cfg_option,
    models: Optional[str] = _models_option,
):
    """Show or install backend dependencies based on current config.

    \b
    mdcore deps status   — show what config needs vs what is installed
    mdcore deps install  — install any missing packages
    """
    from mdcore.core.deps import required_backends, install_packages

    cfg = _load(config, models)
    statuses = required_backends(cfg)

    if action == "status":
        t = Table(title="Dependency status", box=box.SIMPLE, show_header=True)
        t.add_column("Role", style="dim")
        t.add_column("Backend")
        t.add_column("Status")
        t.add_column("Packages needed")
        for s in statuses:
            status_str = "[green]✓ installed[/green]" if s.installed else "[red]✗ missing[/red]"
            pkgs = ", ".join(s.packages) if s.packages else "[dim]in core[/dim]"
            t.add_row(s.role, s.backend, status_str, pkgs)
        console.print(t)

        missing = [s for s in statuses if not s.installed]
        if missing:
            console.print(f"\n[yellow]{len(missing)} backend(s) have missing deps.[/yellow]  Run: [cyan]mdcore deps install[/cyan]")
        else:
            console.print("\n[green]All backends installed.[/green]")

    elif action == "install":
        missing = [s for s in statuses if not s.installed and s.packages]
        if not missing:
            console.print("[green]Nothing to install — all backends already available.[/green]")
            return

        all_pkgs: list[str] = []
        for s in missing:
            console.print(f"  [dim]{s.role}/{s.backend}:[/dim] {', '.join(s.packages)}")
            all_pkgs.extend(s.packages)

        # deduplicate preserving order
        seen: set[str] = set()
        deduped = [p for p in all_pkgs if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]

        console.print(f"\nInstalling {len(deduped)} package(s) into [dim]{sys.executable}[/dim]…")
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True) as p:
            p.add_task("Running pip install…")
            ok = install_packages(deduped, quiet=True)

        if ok:
            console.print("[green]✓ Done.[/green]  Run [cyan]mdcore deps status[/cyan] to verify.")
        else:
            err_console.print("pip install failed. Check output above.")
            raise typer.Exit(1)

    else:
        err_console.print(f"Unknown action '{action}'. Use: status | install")
        raise typer.Exit(1)


# ── mdcore docs / config ──────────────────────────────────────────────────────

@app.command()
def docs(
    topic: str = typer.Argument(
        "config",
        help="Topic to read: config, getting-started, retrieval",
    ),
):
    """Show documentation in the terminal.

    Topics: config, getting-started (start), retrieval (eval)
    """
    from importlib.resources import files

    topic_map: dict[str, str] = {
        "config":          "config-reference.md",
        "conf":            "config-reference.md",
        "getting-started": "getting-started.md",
        "start":           "getting-started.md",
        "setup":           "getting-started.md",
        "retrieval":       "retrieval-and-eval-guide.md",
        "eval":            "retrieval-and-eval-guide.md",
        "tuning":          "retrieval-and-eval-guide.md",
    }
    filename = topic_map.get(topic.lower())
    if not filename:
        available = ", ".join(sorted({k for k in topic_map if k == topic_map[k].replace("-", " ").split(".")[0] or True}))
        err_console.print(f"Unknown topic '{topic}'. Available: {', '.join(sorted(set(topic_map.keys())))}")
        raise typer.Exit(1)

    try:
        content = files("mdcore.docs").joinpath(filename).read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"Could not load docs/{filename}: {exc}")
        raise typer.Exit(1)

    console.print(Markdown(content))


@app.command(name="config")
def config_cmd(
    validate: bool = typer.Option(False, "--validate", help="Validate config file and report errors"),
    config: Optional[str] = _cfg_option,
):
    """Open config file in default editor, or validate it."""
    config_path = Path(config).expanduser() if config else DEFAULT_CONFIG_PATH
    if validate:
        try:
            cfg = load_config(config)
            console.print(f"[green]Config valid:[/green] {config_path}")
        except SystemExit as exc:
            console.print(str(exc))
        return
    # Open in default editor
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil as _shutil
        example = Path(__file__).parent.parent.parent / "config.yaml.example"
        if example.exists():
            _shutil.copy(example, config_path)
            console.print(f"[dim]Copied example config to {config_path}[/dim]")
    import os
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(config_path)])


# ── mdcore gui ────────────────────────────────────────────────────────────────

@app.command()
def gui(config: Optional[str] = _cfg_option):
    """Launch the mdcore TUI (experimental)."""
    try:
        from mdcore.gui.app import run
    except ImportError:
        console.print("[red]Textual not installed.[/red] Run: uv tool install markdowncore-ai --with textual --force")
        raise typer.Exit(1)
    run(config_path=config)


# ── mdcore serve ──────────────────────────────────────────────────────────────

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8765, help="Bind port"),
    config: Optional[str] = _cfg_option,
    reload: bool = typer.Option(False, help="Auto-reload on code change (dev mode)"),
):
    """Start the mdcore REST API server."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn not installed.[/red] Run: pip install \'markdowncore-ai[serve]\'")
        raise typer.Exit(1)

    if config:
        import os as _os
        _os.environ["MDCORE_CONFIG_PATH"] = str(config)

    try:
        from langserve import add_routes as _  # noqa: F401
        langserve_available = True
    except ImportError:
        langserve_available = False

    console.print(f"[green]mdcore API starting on http://{host}:{port}[/green]")
    console.print(f"[dim]Swagger UI: http://{host}:{port}/docs[/dim]")
    if langserve_available:
        console.print(f"[dim]LangServe search: POST http://{host}:{port}/search/invoke[/dim]")

    uvicorn.run(
        "mdcore.serve.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


# ── mdcore mcp ────────────────────────────────────────────────────────────────

@app.command()
def mcp(
    config: Optional[str] = typer.Option(None, "--config", help="Config file path"),
):
    """
    Start the mdcore MCP server (stdio transport).

    Add to Claude Desktop config (~/.../claude_desktop_config.json):

      {
        "mcpServers": {
          "mdcore": {
            "command": "mdcore",
            "args": ["mcp"]
          }
        }
      }
    """
    try:
        import mcp as _mcp  # noqa: F401
    except ImportError:
        err_console = Console(stderr=True)
        err_console.print("[red]mcp not installed.[/red] Run: pip install \'markdowncore-ai[mcp]\'")
        raise typer.Exit(1)

    import asyncio
    from mdcore.mcp_server.server import main

    if config:
        import os as _os
        _os.environ["MDCORE_CONFIG_PATH"] = str(config)

    # MCP server runs over stdio - no stdout output (would corrupt the protocol)
    asyncio.run(main())


# ── mdcore mcp-serve ──────────────────────────────────────────────────────────

@app.command("mcp-serve")
def mcp_serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind"),
    port: int = typer.Option(8766, "--port", "-p", help="Port to listen on"),
    config: Optional[str] = typer.Option(None, "--config", help="Config file path"),
):
    """
    Start the mdcore MCP server with SSE/HTTP transport.

    For clients that connect via URL instead of spawning a subprocess (e.g. Hermes).
    Add to Hermes config.yaml:

      mcp_servers:
        mdcore:
          url: "http://127.0.0.1:8766/sse"

    Run this in a background terminal before starting Hermes:

      mdcore mcp-serve
    """
    if config:
        import os as _os
        _os.environ["MDCORE_CONFIG_PATH"] = str(config)

    from mdcore.mcp_server.server import run_sse
    console.print(f"[green]MCP SSE server starting on http://{host}:{port}/sse[/green]")
    console.print("[dim]Keep this running while Hermes is active. Ctrl+C to stop.[/dim]")
    run_sse(host=host, port=port)

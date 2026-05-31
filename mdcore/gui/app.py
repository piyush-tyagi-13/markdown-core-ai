from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    LoadingIndicator,
    Markdown,
    RichLog,
    Static,
    Tab,
    TabbedContent,
    TabPane,
    TextArea,
)

BANNER = """\
[bold cyan]  ███╗   ███╗██████╗  ██████╗ ██████╗ ██████╗ ███████╗[/]
[bold cyan]  ████╗ ████║██╔══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝[/]
[bold cyan]  ██╔████╔██║██║  ██║██║     ██║   ██║██████╔╝█████╗  [/]
[bold cyan]  ██║╚██╔╝██║██║  ██║██║     ██║   ██║██╔══██╗██╔══╝  [/]
[bold cyan]  ██║ ╚═╝ ██║██████╔╝╚██████╗╚██████╔╝██║  ██║███████╗[/]
[bold cyan]  ╚═╝     ╚═╝╚═════╝  ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝[/]
[dim]  Markdown Core (Classification, Organisation, Retrieval & Entry)[/dim]
"""


class AppBanner(Static):
    DEFAULT_CSS = """
    AppBanner {
        background: $surface-darken-2;
        padding: 1 2;
        border-bottom: heavy $primary;
        text-align: left;
        height: 10;
        overflow: hidden hidden;
    }
    """

    def render(self):
        return BANNER


# ── helpers ───────────────────────────────────────────────────────────────────

def _backend_label(backend: str, model: str, aggregator_category: str | None) -> str:
    if backend == "aggregator":
        return f"aggregator ({aggregator_category or 'general_purpose'})"
    return model or "(default)"


def _aggregator_pool_lines(category: str) -> list[str]:
    """Return active key quota line for TUI status panel."""
    try:
        from llm_keypool import AggregatorChat
        k = AggregatorChat(category=category).current_key()
        if not k:
            return [f"  *(no keys registered for '{category}')*"]
        cd = k.get("cooldown_until")
        from datetime import datetime, timezone
        cd_dt = datetime.fromisoformat(cd.replace("Z", "+00:00")) if cd else None
        is_active = cd_dt and cd_dt > datetime.now(timezone.utc)
        cd_str = f"cooldown until {cd[:19]}" if is_active else "available"
        return [
            f"  **{k['provider']}** `{k['model']}` "
            f"slot {k['cycle_position']}/{k['rotate_every']} "
            f"req={k['requests_today']} tok={k['tokens_used_today']} - {cd_str}"
        ]
    except Exception as e:
        return [f"  *(key status unavailable: {e})*"]


def _query_slug(topic: str) -> str:
    slug = topic.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60]


# ── Confirm modal ─────────────────────────────────────────────────────────────

class ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no modal."""

    BINDINGS = [
        Binding("a", "approve", "Approve"),
        Binding("r", "reject", "Reject"),
        Binding("escape", "reject", "Reject"),
    ]

    def __init__(self, proposal_text: str) -> None:
        super().__init__()
        self._proposal_text = proposal_text

    def compose(self) -> ComposeResult:
        with Container(id="confirm-container"):
            yield Static("[bold]mdcore proposal[/bold]", id="confirm-title")
            yield Markdown(self._proposal_text, id="confirm-body")
            with Horizontal(id="confirm-buttons"):
                yield Button("Approve [A]", id="btn-approve", variant="success")
                yield Button("Reject [R]", id="btn-reject", variant="error")

    @on(Button.Pressed, "#btn-approve")
    def action_approve(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-reject")
    def action_reject(self) -> None:
        self.dismiss(False)


# ── Main app ──────────────────────────────────────────────────────────────────

class MdCoreApp(App):
    """mdcore TUI - Markdown CORE AI"""

    TITLE = "mdcore"
    SUB_TITLE = "Markdown CORE AI"

    CSS = """
    Screen {
        background: $surface;
        overflow: hidden hidden;
        layout: vertical;
    }

    TabbedContent {
        height: 1fr;
    }

    TabPane {
        height: 1fr;
    }

    /* ── Search tab ── */
    #search-pane {
        padding: 1 2;
    }
    #search-row {
        height: 3;
        margin-bottom: 1;
    }
    #search-input {
        width: 1fr;
        margin-right: 1;
    }
    #btn-search {
        width: 12;
    }
    #btn-raw {
        width: 10;
    }
    #search-loading {
        height: 3;
        display: none;
    }
    #search-loading.visible {
        display: block;
    }
    #search-results {
        border: round $primary;
        padding: 1;
        height: 1fr;
    }
    #search-result-scroll {
        height: 1fr;
    }

    /* ── Ingest tab ── */
    #ingest-pane {
        padding: 1 2;
    }
    #ingest-doc {
        height: 12;
        margin-bottom: 1;
        border: round $primary;
    }
    #file-row {
        height: 3;
        margin-bottom: 1;
    }
    #file-input {
        width: 1fr;
        margin-right: 1;
    }
    #btn-load-file {
        width: 14;
    }
    #btn-classify {
        width: 14;
        margin-bottom: 1;
    }
    #ingest-loading {
        height: 3;
        display: none;
    }
    #ingest-loading.visible {
        display: block;
    }
    #ingest-result {
        border: round $primary;
        padding: 1;
        height: 1fr;
    }
    #ingest-result-scroll {
        height: 1fr;
    }

    /* ── Index tab ── */
    #index-pane {
        padding: 1 2;
    }
    #index-buttons {
        height: 3;
        margin-bottom: 1;
    }
    #btn-index {
        width: 18;
        margin-right: 1;
    }
    #btn-force-index {
        width: 22;
    }
    #index-loading {
        height: 3;
        display: none;
    }
    #index-loading.visible {
        display: block;
    }
    #index-log {
        border: round $primary;
        height: 1fr;
        padding: 0 1;
    }

    /* ── Status tab ── */
    #status-pane {
        padding: 1 2;
    }
    #status-scroll {
        height: 1fr;
        margin-bottom: 1;
        border: round $primary-darken-2;
        padding: 0 1;
    }
    #status-content {
        height: auto;
    }
    #btn-refresh-status {
        width: 16;
        margin-top: 1;
    }
    #status-loading {
        height: 3;
        display: none;
    }
    #status-loading.visible {
        display: block;
    }

    /* ── Vault Map tab ── */
    #vault-map-pane {
        padding: 1 2;
    }
    #vault-map-buttons {
        height: 3;
        margin-bottom: 1;
    }
    #btn-rebuild-map {
        width: 22;
        margin-right: 1;
    }
    #btn-save-map {
        width: 22;
    }
    #vault-map-loading {
        height: 3;
        display: none;
    }
    #vault-map-loading.visible {
        display: block;
    }
    #vault-map-scroll {
        border: round $primary;
        height: 1fr;
        padding: 1;
    }
    .map-row {
        height: 3;
        margin-bottom: 0;
    }
    .map-folder-label {
        width: 36;
        padding: 1 1 0 0;
        color: $accent;
        text-style: bold;
    }
    .map-desc-input {
        width: 1fr;
    }
    #vault-map-status {
        height: 1;
        color: $success;
        margin-top: 1;
    }

    /* ── Confirm modal ── */
    ConfirmScreen {
        align: center middle;
    }
    #confirm-container {
        width: 80;
        max-height: 40;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #confirm-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
        color: $accent;
    }
    #confirm-body {
        height: auto;
        max-height: 28;
        overflow-y: auto;
    }
    #confirm-buttons {
        height: 3;
        margin-top: 1;
        align: center middle;
    }
    #btn-approve {
        width: 18;
        margin-right: 2;
    }
    #btn-reject {
        width: 18;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+s", "search_focus", "Search"),
        Binding("ctrl+i", "ingest_focus", "Ingest"),
    ]

    def __init__(self, config_path: Optional[str] = None) -> None:
        super().__init__()
        self._config_path = config_path
        self._cfg = None
        self._store = None
        self._engine = None
        # Pending ingest state - held between classify and approve
        self._pending_proposal = None
        self._pending_summary = None
        self._pending_existing_content = None
        # Vault map state - ordered list of folder paths matching input widget indices
        self._vault_map_folders: list[str] = []

    def _load_cfg(self):
        if self._cfg is None:
            from mdcore.config.loader import load_config
            self._cfg = load_config(self._config_path)
        return self._cfg

    def _get_store(self):
        if self._store is None:
            from mdcore.store.vector_store import VectorStore
            cfg = self._load_cfg()
            self._store = VectorStore(cfg.vector_store)
        return self._store

    def _get_engine(self):
        if self._engine is None:
            from mdcore.core.indexer.embedding_engine import EmbeddingEngine
            cfg = self._load_cfg()
            self._engine = EmbeddingEngine(cfg.embeddings)
        return self._engine

    def compose(self) -> ComposeResult:
        yield AppBanner()
        with TabbedContent(initial="search"):
            with TabPane("Search", id="search"):
                with Vertical(id="search-pane"):
                    with Horizontal(id="search-row"):
                        yield Input(
                            placeholder="Enter topic to search...",
                            id="search-input",
                        )
                        yield Button("Search", id="btn-search", variant="primary")
                        yield Button("Raw", id="btn-raw")
                    yield LoadingIndicator(id="search-loading")
                    with Container(id="search-results"):
                        with ScrollableContainer(id="search-result-scroll"):
                            yield Markdown("*Results will appear here.*", id="search-md")

            with TabPane("Ingest", id="ingest"):
                with Vertical(id="ingest-pane"):
                    yield TextArea(
                        "",
                        id="ingest-doc",
                        show_line_numbers=False,
                    )
                    with Horizontal(id="file-row"):
                        yield Input(
                            placeholder="Or enter file path...",
                            id="file-input",
                        )
                        yield Button("Load File", id="btn-load-file")
                    yield Button("Classify + Propose", id="btn-classify", variant="primary")
                    yield LoadingIndicator(id="ingest-loading")
                    with Container(id="ingest-result"):
                        with ScrollableContainer(id="ingest-result-scroll"):
                            yield Markdown("*Paste a document above, then click Classify + Propose.*", id="ingest-md")

            with TabPane("Index", id="index"):
                with Vertical(id="index-pane"):
                    with Horizontal(id="index-buttons"):
                        yield Button("Index Vault", id="btn-index", variant="primary")
                        yield Button("Force Reindex", id="btn-force-index", variant="warning")
                    yield LoadingIndicator(id="index-loading")
                    yield RichLog(id="index-log", highlight=True, markup=True)

            with TabPane("Status", id="status"):
                with Vertical(id="status-pane"):
                    with ScrollableContainer(id="status-scroll"):
                        yield Static("", id="status-content")
                    yield Button("Refresh", id="btn-refresh-status")
                    yield LoadingIndicator(id="status-loading")

            with TabPane("Vault Map", id="vault-map"):
                with Vertical(id="vault-map-pane"):
                    with Horizontal(id="vault-map-buttons"):
                        yield Button("Rebuild Folders", id="btn-rebuild-map")
                        yield Button("Save Descriptions", id="btn-save-map", variant="primary")
                    yield LoadingIndicator(id="vault-map-loading")
                    with ScrollableContainer(id="vault-map-scroll"):
                        yield Static("Switch to this tab to load vault folders.", id="vault-map-placeholder")
                    yield Static("", id="vault-map-status")

        yield Footer()

    def on_mount(self) -> None:
        self._load_status()

    def on_tabbed_content_tab_activated(self, event) -> None:
        active = self.query_one(TabbedContent).active
        if active == "vault-map" and not self._vault_map_folders:
            self._load_vault_map()

    # ── Search ────────────────────────────────────────────────────────────────

    def action_search_focus(self) -> None:
        self.query_one("#search-input").focus()

    @on(Button.Pressed, "#btn-search")
    def on_search(self) -> None:
        query = self.query_one("#search-input", Input).value.strip()
        if query:
            self._run_search(query, raw=False)

    @on(Button.Pressed, "#btn-raw")
    def on_search_raw(self) -> None:
        query = self.query_one("#search-input", Input).value.strip()
        if query:
            self._run_search(query, raw=True)

    @on(Input.Submitted, "#search-input")
    def on_search_submitted(self) -> None:
        query = self.query_one("#search-input", Input).value.strip()
        if query:
            self._run_search(query, raw=False)

    @work(thread=True)
    def _run_search(self, topic: str, raw: bool) -> None:
        loading = self.query_one("#search-loading")
        self.call_from_thread(loading.add_class, "visible")
        self.call_from_thread(
            self.query_one("#search-md", Markdown).update,
            "*Searching...*"
        )

        try:
            from mdcore.core.retriever.keyword_prefilter import KeywordPreFilter
            from mdcore.core.retriever.vector_searcher import VectorSearcher
            from mdcore.core.retriever.chunk_grouper import group_by_source
            from mdcore.core.retriever.chunk_stitcher import stitch
            from mdcore.core.retriever.source_ranker import rank_sources
            from mdcore.core.retriever.context_assembler import assemble
            from mdcore.core.retriever.context_formatter import format_context, raw_text_for_synthesis

            cfg = self._load_cfg()
            store = self._get_store()
            engine = self._get_engine()

            all_chunks = store.all_chunks()
            if not all_chunks:
                self.call_from_thread(
                    self.query_one("#search-md", Markdown).update,
                    "**Index is empty.** Run `mdcore index` first."
                )
                return

            candidate_sources = None
            if cfg.retriever.keyword_prefilter:
                prefilter = KeywordPreFilter(
                    cfg.retriever.keyword_prefilter_min_score,
                    owner_name=cfg.vault.owner_name,
                    mode=cfg.retriever.keyword_prefilter_mode,
                )
                candidate_sources = prefilter.filter(topic, all_chunks) or None

            vector_query = topic
            if cfg.vault.owner_name:
                owner_words_lc = {w.lower() for w in cfg.vault.owner_name.split()}
                stripped = " ".join(w for w in topic.split() if w.lower() not in owner_words_lc)
                if stripped.strip():
                    vector_query = stripped.strip()

            searcher = VectorSearcher(store, engine, cfg.retriever)
            chunks = searcher.search(vector_query, candidate_sources)

            if not chunks:
                self.call_from_thread(
                    self.query_one("#search-md", Markdown).update,
                    f"**No results found** for '{topic}'.\n\nTry lowering `similarity_threshold` in config."
                )
                return

            groups = group_by_source(chunks)
            passages_by_source = {sf: stitch(sf, c, cfg.retriever) for sf, c in groups.items()}
            ranked = rank_sources(passages_by_source)
            ctx = assemble(topic, ranked, cfg.retriever)

            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            source_lines = "\n".join(
                f"- [{i}] {sf}" for i, (sf, _) in enumerate(ctx.primary, 1)
            )

            if raw:
                raw_output = format_context(ctx, cfg.retriever)
                md_content = (
                    f"# {topic}\n\n"
                    f"*{now_str} - {ctx.source_count} sources - raw excerpts*\n\n"
                    f"## Sources\n\n{source_lines}\n\n---\n\n{raw_output}"
                )
                mode_label = "raw"
            else:
                from mdcore.llm.llm_layer import LLMLayer
                llm = LLMLayer(cfg.llm)
                raw_text = raw_text_for_synthesis(ctx)
                synth_model = cfg.llm.synthesise_model or cfg.llm.model

                self.call_from_thread(
                    self.query_one("#search-md", Markdown).update,
                    f"*Synthesising with {synth_model}...*"
                )

                briefing = llm.synthesise(topic, raw_text)
                raw_output = format_context(ctx, cfg.retriever)
                md_content = (
                    f"# {topic}\n\n"
                    f"*{now_str} - {ctx.source_count} sources - synthesised by {synth_model}*\n\n"
                    f"> Verify claims against raw excerpts below.\n\n"
                    f"## Sources\n\n{source_lines}\n\n---\n\n"
                    f"## Briefing\n\n{briefing}\n\n---\n\n"
                    f"## Raw Excerpts\n\n{raw_output}"
                )
                mode_label = f"synthesised - {synth_model}"

            # Write output file
            out_dir = Path(cfg.vault.path).expanduser() / "mdcore-output"
            out_dir.mkdir(parents=True, exist_ok=True)
            date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            filename = f"{date_prefix}-{_query_slug(topic)}.md"
            out_path = out_dir / filename
            out_path.write_text(md_content, encoding="utf-8")

            display_md = md_content + f"\n\n---\n*Saved to `{out_path}`*"
            self.call_from_thread(
                self.query_one("#search-md", Markdown).update,
                display_md
            )

        except Exception as exc:
            self.call_from_thread(
                self.query_one("#search-md", Markdown).update,
                f"**Error:** {exc}"
            )
        finally:
            self.call_from_thread(loading.remove_class, "visible")

    # ── Ingest ────────────────────────────────────────────────────────────────

    def action_ingest_focus(self) -> None:
        self.query_one("#ingest-doc").focus()

    @on(Button.Pressed, "#btn-load-file")
    def on_load_file(self) -> None:
        file_path = self.query_one("#file-input", Input).value.strip()
        if not file_path:
            return
        p = Path(file_path).expanduser()
        if not p.exists():
            self.query_one("#ingest-md", Markdown).update(f"**File not found:** `{file_path}`")
            return
        content = p.read_text(encoding="utf-8", errors="ignore")
        self.query_one("#ingest-doc", TextArea).load_text(content)

    @on(Button.Pressed, "#btn-classify")
    def on_classify(self) -> None:
        doc = self.query_one("#ingest-doc", TextArea).text.strip()
        if not doc:
            self.query_one("#ingest-md", Markdown).update("**Paste a document first.**")
            return
        self._run_classify(doc)

    @work(thread=True)
    def _run_classify(self, summary: str) -> None:
        loading = self.query_one("#ingest-loading")
        self.call_from_thread(loading.add_class, "visible")
        self.call_from_thread(
            self.query_one("#ingest-md", Markdown).update,
            "*Classifying...*"
        )

        try:
            from mdcore.core.ingester.summary_receiver import SummaryReceiver
            from mdcore.core.ingester.summary_embedder import SummaryEmbedder
            from mdcore.core.ingester.classification_engine import ClassificationEngine
            from mdcore.core.ingester.folder_router import FolderRouter
            from mdcore.core.ingester.conflict_detector import ConflictDetector
            from mdcore.core.ingester.proposal_generator import ProposalGenerator
            from mdcore.llm.llm_layer import LLMLayer
            from datetime import date

            cfg = self._load_cfg()
            store = self._get_store()
            engine = self._get_engine()
            llm = LLMLayer(cfg.llm)

            receiver = SummaryReceiver(cfg.ingester)
            summary_obj = receiver.receive_from_text(summary)

            embedder = SummaryEmbedder(engine)
            embs = embedder.embed(summary_obj)

            classify_engine = ClassificationEngine(store, llm, cfg.ingester)
            decision = classify_engine.classify(embs.full, summary_obj)

            existing_content = ""
            conflicts = []
            if decision.action == "update" and decision.target_file:
                vault_path = Path(cfg.vault.path).expanduser()
                existing_path = vault_path / decision.target_file
                if existing_path.exists():
                    existing_content = existing_path.read_text(encoding="utf-8", errors="ignore")
                    detector = ConflictDetector(engine, cfg.ingester)
                    conflicts = detector.detect(existing_content, summary_obj)

            folder = ""
            if decision.action == "new":
                self.call_from_thread(
                    self.query_one("#ingest-md", Markdown).update,
                    "*Routing to folder...*"
                )
                router = FolderRouter(cfg.vault, cfg.ingester, llm)
                folder, _ = router.route(summary_obj, top_scores=decision.top_scores)

            fm_updates = {"updated": str(date.today()), "tags": [], "related": []}

            self.call_from_thread(
                self.query_one("#ingest-md", Markdown).update,
                "*Generating proposal...*"
            )

            generator = ProposalGenerator(llm)
            proposal = generator.generate(decision, summary_obj, existing_content, conflicts, folder, fm_updates)

            # Store pending state for approve step
            self._pending_proposal = proposal
            self._pending_summary = summary_obj
            self._pending_existing_content = existing_content

            # Build markdown display for proposal
            lines = [
                f"## Proposal\n",
                f"**Action:** `{proposal.action.upper()}` {'existing file' if proposal.action == 'update' else 'new file'}",
            ]
            if proposal.target_file:
                lines.append(f"**Target:** `{proposal.target_file}`")
            elif proposal.suggested_folder:
                lines.append(f"**Folder:** `{proposal.suggested_folder}`")
            lines.append(f"**Confidence:** {proposal.confidence:.2f}")
            if decision.used_llm:
                lines.append("*LLM consulted for ambiguous classification.*")
            lines.append(f"\n**Proposed changes:**\n")
            for line in proposal.proposal_text.strip().splitlines():
                lines.append(f"- {line.lstrip('- •').strip()}")
            if conflicts:
                lines.append(f"\n**Conflicts detected ({len(conflicts)}):**\n")
                for c in conflicts[:3]:
                    lines.append(f"- Existing: *\"{c.existing_sentence[:80]}\"*")
                    lines.append(f"  Incoming: *\"{c.incoming_sentence[:80]}\"*")
                    lines.append(f"  Similarity: {c.similarity:.2f}")
            lines.append(f"\n---\n*Press **Approve** to write to vault, or **Reject** to discard.*")

            proposal_md = "\n".join(lines)
            self.call_from_thread(
                self.query_one("#ingest-md", Markdown).update,
                proposal_md
            )
            # Show confirm modal from main thread
            self.call_from_thread(self._show_confirm_modal, proposal_md)

        except Exception as exc:
            self.call_from_thread(
                self.query_one("#ingest-md", Markdown).update,
                f"**Error:** {exc}"
            )
        finally:
            self.call_from_thread(loading.remove_class, "visible")

    def _show_confirm_modal(self, proposal_md: str) -> None:
        def on_result(approved: bool) -> None:
            if approved:
                self._run_write()
            else:
                self.query_one("#ingest-md", Markdown).update(
                    proposal_md + "\n\n*Rejected. No changes made.*"
                )

        self.push_screen(ConfirmScreen(proposal_md), on_result)

    @work(thread=True)
    def _run_write(self) -> None:
        loading = self.query_one("#ingest-loading")
        self.call_from_thread(loading.add_class, "visible")

        try:
            from mdcore.core.writer.backup_manager import BackupManager
            from mdcore.core.writer.frontmatter_injector import FrontmatterInjector
            from mdcore.core.writer.file_writer import FileWriter
            from mdcore.core.writer.index_trigger import IndexTrigger
            from mdcore.core.indexer.manifest_manager import ManifestManager
            from mdcore.core.indexer.document_loader import DocumentLoader
            from mdcore.core.indexer.text_splitter import TextSplitter
            from mdcore.core.indexer.index_writer import IndexWriter
            import re

            cfg = self._load_cfg()
            proposal = self._pending_proposal
            summary = self._pending_summary
            existing_content = self._pending_existing_content
            vault_path = Path(cfg.vault.path).expanduser()

            backup_mgr = BackupManager(cfg.writer.backup)
            fm_injector = FrontmatterInjector(cfg.writer.frontmatter)
            file_writer = FileWriter(cfg.vault, cfg.writer)
            store = self._get_store()
            engine = self._get_engine()

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
                result_msg = f"**Updated:** `{proposal.target_file}`\n\nReindexed successfully."
            else:
                # Derive filename from summary
                filename = "ingested-note.md"
                for line in (summary if isinstance(summary, str) else str(summary)).splitlines():
                    m = re.match(r"^#{1,3}\s+(.+)$", line)
                    if m:
                        title = m.group(1).strip()
                        safe = re.sub(r"[^a-z0-9\- ]", "", title.lower())
                        filename = safe.replace(" ", "-")[:50] + ".md"
                        break
                new_path = file_writer.create(proposal.suggested_folder, filename, summary)
                trigger.reindex(new_path)
                result_msg = f"**Created:** `{new_path}`\n\nReindexed successfully."

            self.call_from_thread(
                self.query_one("#ingest-md", Markdown).update,
                result_msg
            )
            # Clear the text area
            self.call_from_thread(
                self.query_one("#ingest-doc", TextArea).load_text, ""
            )
            self._pending_proposal = None
            self._pending_summary = None
            self._pending_existing_content = None

        except Exception as exc:
            self.call_from_thread(
                self.query_one("#ingest-md", Markdown).update,
                f"**Write failed:** {exc}"
            )
        finally:
            self.call_from_thread(loading.remove_class, "visible")

    # ── Index ─────────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-index")
    def on_index(self) -> None:
        self._run_index(force=False)

    @on(Button.Pressed, "#btn-force-index")
    def on_force_index(self) -> None:
        self._run_index(force=True)

    def _log(self, msg: str) -> None:
        """Write a line to the index log from any thread."""
        self.call_from_thread(
            self.query_one("#index-log", RichLog).write, msg
        )

    @work(thread=True)
    def _run_index(self, force: bool) -> None:
        from datetime import datetime, timezone

        loading = self.query_one("#index-loading")
        log = self.query_one("#index-log", RichLog)
        self.call_from_thread(log.clear)
        self.call_from_thread(loading.add_class, "visible")

        try:
            import shutil
            from mdcore.core.indexer.vault_scanner import VaultScanner
            from mdcore.core.indexer.manifest_manager import ManifestManager
            from mdcore.core.indexer.document_loader import DocumentLoader
            from mdcore.core.indexer.text_splitter import TextSplitter
            from mdcore.core.indexer.index_writer import IndexWriter
            from mdcore.config.loader import expand_path

            cfg = self._load_cfg()

            if force:
                manifest_path = expand_path(cfg.manifest.path)
                chroma_path = Path(cfg.vector_store.persist_path).expanduser()
                cache_path = expand_path(cfg.embeddings.cache_path) / "embed_cache.pkl"
                if manifest_path.exists():
                    manifest_path.unlink()
                    self._log(f"[dim]Deleted manifest: {manifest_path}[/dim]")
                if chroma_path.exists():
                    shutil.rmtree(chroma_path)
                    self._store = None  # reset cached store
                    self._log(f"[dim]Deleted vector store: {chroma_path}[/dim]")
                if cache_path.exists():
                    cache_path.unlink()
                    self._engine = None  # reset cached engine
                    self._log(f"[dim]Deleted embed cache: {cache_path}[/dim]")
                self._log("[yellow]Force reindex — all files will be re-indexed.[/yellow]")

            scanner = VaultScanner(cfg.vault, cfg.indexer)
            manifest = ManifestManager(cfg.manifest, cfg.vault)
            loader = DocumentLoader(cfg.vault)
            splitter = TextSplitter(cfg.indexer)
            store = self._get_store()
            engine = self._get_engine()
            writer = IndexWriter(store, engine, cfg.indexer)

            eligible = scanner.scan()
            self._log(f"Scanned vault — [cyan]{len(eligible)}[/cyan] eligible files")

            diff = manifest.diff(eligible)

            if diff.total_changes == 0:
                self._log("[green]Index is up to date — nothing to do.[/green]")
                return

            for p in diff.new_files:
                self._log(f"  [green][+][/green] {p.name}")
            for p in diff.modified_files:
                self._log(f"  [yellow][~][/yellow] {p.name}")
            for k in diff.deleted_files:
                self._log(f"  [red][-][/red] {k}")

            self._log(
                f"\nIndexing [cyan]{len(diff.new_files + diff.modified_files)}[/cyan] files, "
                f"removing [red]{len(diff.deleted_files)}[/red]..."
            )

            files_to_index = diff.new_files + diff.modified_files
            start = datetime.now(timezone.utc)
            skipped = []

            for i, path in enumerate(files_to_index, 1):
                try:
                    doc = loader.load(path)
                    chunks = splitter.split(doc)
                    source_file = doc.metadata.get("source_file", str(path))
                    writer.write(chunks, source_file)
                    manifest.update(path)
                    self._log(f"  [{i}/{len(files_to_index)}] [dim]{path.name}[/dim] → {len(chunks)} chunks")
                except Exception as exc:
                    skipped.append((path, str(exc)))
                    self._log(f"  [yellow]⚠ Skipped:[/yellow] {path.name} — {exc}")

            for key in diff.deleted_files:
                store.delete(key)
                manifest.remove(key)

            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            indexed_count = len(files_to_index) - len(skipped)
            self._log(
                f"\n[green]Done.[/green] {indexed_count} indexed, "
                f"{len(diff.deleted_files)} removed in {elapsed:.1f}s"
            )
            if skipped:
                self._log(f"[yellow]{len(skipped)} skipped.[/yellow]")

        except Exception as exc:
            self._log(f"[red]Error:[/red] {exc}")
        finally:
            self.call_from_thread(loading.remove_class, "visible")

    # ── Vault Map ─────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-rebuild-map")
    def on_rebuild_map(self) -> None:
        self._vault_map_folders = []
        self._load_vault_map()

    @on(Button.Pressed, "#btn-save-map")
    def on_save_map(self) -> None:
        self._save_vault_map()

    @work(thread=True)
    def _load_vault_map(self) -> None:
        loading = self.query_one("#vault-map-loading")
        self.call_from_thread(loading.add_class, "visible")

        try:
            cfg = self._load_cfg()
            from mdcore.core.vault_map import VaultMap
            from pathlib import Path

            vault_path = Path(cfg.vault.path).expanduser()
            vmap = VaultMap(vault_path)
            # write_template picks up new folders and preserves existing descriptions
            vmap.write_template()
            folders = vmap.all_vault_folders()
            descriptions = vmap.folder_descriptions()

            self._vault_map_folders = folders

            def _rebuild_rows(folders=folders, descriptions=descriptions):
                scroll = self.query_one("#vault-map-scroll")
                # Remove placeholder/old rows synchronously from main thread
                for child in list(scroll.children):
                    child.remove()
                for i, folder in enumerate(folders):
                    desc = descriptions.get(folder, "")
                    row = Horizontal(classes="map-row")
                    label = Label(folder, classes="map-folder-label")
                    inp = Input(
                        value=desc,
                        placeholder="Describe what belongs here...",
                        id=f"map-input-{i}",
                        classes="map-desc-input",
                    )
                    scroll.mount(row)
                    row.mount(label)
                    row.mount(inp)

            self.call_from_thread(_rebuild_rows)

            self.call_from_thread(
                self.query_one("#vault-map-status", Static).update,
                f"{len(folders)} folders loaded. Edit descriptions and click Save."
            )

        except Exception as exc:
            self.call_from_thread(
                self.query_one("#vault-map-status", Static).update,
                f"[red]Error:[/red] {exc}"
            )
        finally:
            self.call_from_thread(loading.remove_class, "visible")

    @work(thread=True)
    def _save_vault_map(self) -> None:
        loading = self.query_one("#vault-map-loading")
        self.call_from_thread(loading.add_class, "visible")

        try:
            cfg = self._load_cfg()
            from mdcore.core.vault_map import VaultMap
            from pathlib import Path

            vault_path = Path(cfg.vault.path).expanduser()
            vmap = VaultMap(vault_path)

            def _collect_and_save():
                for i, folder in enumerate(self._vault_map_folders):
                    try:
                        inp = self.query_one(f"#map-input-{i}", Input)
                        desc = inp.value.strip()
                        if desc:
                            vmap.set_description(folder, desc)
                        else:
                            vmap.remove_description(folder)
                    except Exception:
                        pass
                vmap.save()
                self.query_one("#vault-map-status", Static).update(
                    "[green]Saved.[/green] Run Index to apply changes to routing."
                )

            self.call_from_thread(_collect_and_save)

        except Exception as exc:
            self.call_from_thread(
                self.query_one("#vault-map-status", Static).update,
                f"[red]Save failed:[/red] {exc}"
            )
        finally:
            self.call_from_thread(loading.remove_class, "visible")

    # ── Status ────────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-refresh-status")
    def on_refresh_status(self) -> None:
        self._load_status()

    @work(thread=True)
    def _load_status(self) -> None:
        loading = self.query_one("#status-loading")
        self.call_from_thread(loading.add_class, "visible")

        try:
            from rich.table import Table
            from rich.text import Text
            from rich.console import Group
            from rich.rule import Rule
            from rich.padding import Padding
            from mdcore.core.indexer.manifest_manager import ManifestManager
            from mdcore.core.indexer.vault_scanner import VaultScanner

            cfg = self._load_cfg()
            manifest = ManifestManager(cfg.manifest, cfg.vault)
            store = self._get_store()

            scanner = VaultScanner(cfg.vault, cfg.indexer)
            eligible_files = scanner.scan()
            eligible = len(eligible_files)
            indexed = len(manifest._data)

            try:
                chunk_count = store._count()
            except Exception:
                chunk_count = 0

            try:
                drift = manifest.drift_count(eligible_files)
                drift_color = "yellow" if drift > 0 else "green"
                drift_str = f"[{drift_color}]{drift} files changed[/{drift_color}]" if drift > 0 else "[green]Up to date[/green]"
            except Exception:
                drift_str = "[dim]Unknown[/dim]"

            index_pct = int(indexed / eligible * 100) if eligible else 100
            health_color = "green" if drift == 0 else "yellow"

            def _kv(key: str, val: str) -> Text:
                t = Text()
                t.append(f"  {key:<22}", style="dim cyan")
                t.append_text(Text.from_markup(val))
                return t

            llm_label = _backend_label(cfg.llm.backend, cfg.llm.model, cfg.llm.aggregator_category)
            emb_label = _backend_label(
                cfg.embeddings.backend,
                cfg.embeddings.api_model or cfg.embeddings.local_model,
                None,
            )

            # ── vault section ──────────────────────────────────────────────
            vault_rule = Rule("[bold cyan]  Vault[/bold cyan]", style="cyan", align="left")
            vault_path_short = cfg.vault.path.replace(str(Path.home()), "~")
            vault_lines = [
                _kv("Path", f"[white]{vault_path_short}[/white]"),
                _kv("Owner", f"[white]{cfg.vault.owner_name or '(not set)'}[/white]"),
            ]

            # ── index health section ───────────────────────────────────────
            index_rule = Rule("[bold cyan]  Index Health[/bold cyan]", style="cyan", align="left")
            index_lines = [
                _kv("Eligible files", f"[white]{eligible}[/white]"),
                _kv("Indexed files",  f"[{health_color}]{indexed}[/{health_color}] [dim]({index_pct}%)[/dim]"),
                _kv("Total chunks",   f"[white]{chunk_count}[/white]"),
                _kv("Drift",          drift_str),
            ]

            # ── backends section ──────────────────────────────────────────
            backend_rule = Rule("[bold cyan]  Backends[/bold cyan]", style="cyan", align="left")
            backend_lines = [
                _kv("LLM",        f"[white]{cfg.llm.backend}[/white] [dim]/[/dim] [cyan]{llm_label}[/cyan]"),
                _kv("Embeddings", f"[white]{cfg.embeddings.backend}[/white] [dim]/[/dim] [cyan]{emb_label}[/cyan]"),
            ]

            renderables = [
                Padding("", (1, 0, 0, 0)),
                vault_rule, *vault_lines,
                Padding("", (1, 0, 0, 0)),
                index_rule, *index_lines,
                Padding("", (1, 0, 0, 0)),
                backend_rule, *backend_lines,
            ]

            # ── key pool section (aggregator only) ────────────────────────
            if cfg.llm.backend == "aggregator":
                pool_rule = Rule("[bold cyan]  Key Pool[/bold cyan]", style="cyan", align="left")
                renderables.append(Padding("", (1, 0, 0, 0)))
                renderables.append(pool_rule)
                try:
                    from llm_keypool import AggregatorChat
                    k = AggregatorChat(
                        category=cfg.llm.aggregator_category or "general_purpose"
                    ).current_key()
                    if k:
                        cd = k.get("cooldown_until")
                        from datetime import datetime, timezone
                        cd_dt = datetime.fromisoformat(cd.replace("Z", "+00:00")) if cd else None
                        is_active = cd_dt and cd_dt > datetime.now(timezone.utc)
                        cd_text = (
                            Text(f"  cooldown until {cd[:19]}", style="yellow")
                            if is_active
                            else Text("  available", style="green")
                        )
                        renderables += [
                            _kv("Provider", f"[white]{k['provider']}[/white]"),
                            _kv("Model",    f"[cyan]{k['model']}[/cyan]"),
                            _kv("Slot",     f"[white]{k['cycle_position']}/{k['rotate_every']}[/white]"),
                            _kv("Req today",  f"[white]{k['requests_today']}[/white]"),
                            _kv("Tok today",  f"[white]{k['tokens_used_today']}[/white]"),
                            cd_text,
                        ]
                    else:
                        renderables.append(Text("  No keys registered", style="dim"))
                except Exception as e:
                    renderables.append(Text(f"  unavailable: {e}", style="dim red"))

            renderables.append(Padding("", (1, 0, 0, 0)))

            self.call_from_thread(
                self.query_one("#status-content", Static).update,
                Group(*renderables),
            )

        except Exception as exc:
            self.call_from_thread(
                self.query_one("#status-content", Static).update,
                f"[red bold]Error loading status:[/red bold] {exc}",
            )
        finally:
            self.call_from_thread(loading.remove_class, "visible")


def run(config_path: Optional[str] = None) -> None:
    app = MdCoreApp(config_path=config_path)
    app.run()


if __name__ == "__main__":
    run()

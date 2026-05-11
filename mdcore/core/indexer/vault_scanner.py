from __future__ import annotations
from pathlib import Path

from markdown_it import MarkdownIt

from mdcore.config.models import VaultConfig, IndexerConfig
from mdcore.utils.logging import get_logger

log = get_logger("indexer.scanner")
_md = MarkdownIt()


def _has_structure_signals(text: str, min_signals: int) -> bool:
    tokens = _md.parse(text)
    count = sum(1 for t in tokens if t.type in ("heading_open", "paragraph_open", "bullet_list_open"))
    return count >= min_signals


class VaultScanner:
    def __init__(self, vault_cfg: VaultConfig, indexer_cfg: IndexerConfig) -> None:
        self._vault_path = Path(vault_cfg.path).expanduser()
        # Normalise to lowercase for case-insensitive matching.
        # mdcore-output is always excluded — it is mdcore's own output folder
        # inside the vault and must never be indexed regardless of user config.
        self._excluded_folders = {f.lower() for f in vault_cfg.excluded_folders} | {"mdcore-output"}
        self._excluded_extensions = {e.lower() for e in vault_cfg.excluded_extensions}
        self._min_word_count = indexer_cfg.min_word_count
        self._min_signals = indexer_cfg.min_structure_signals
        self._skip_structure_for = set(indexer_cfg.skip_structure_check_for)
        # Which non-markdown extensions are enabled by user config
        self._enabled_extras: set[str] = set()
        if vault_cfg.index_pdf:
            self._enabled_extras.add(".pdf")
        if vault_cfg.index_docx:
            self._enabled_extras.add(".docx")
        if vault_cfg.index_txt:
            self._enabled_extras.add(".txt")

    def scan(self) -> list[Path]:
        eligible: list[Path] = []
        for path in self._vault_path.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix == ".md":
                pass  # always eligible to attempt
            elif suffix in self._enabled_extras:
                pass  # user opted in
            else:
                continue
            if path.name == ".mdcore-meta.yaml":
                continue
            if suffix in self._excluded_extensions:
                continue
            if any(part.lower() in self._excluded_folders for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            words = len(text.split())
            if words < self._min_word_count:
                log.debug("Skipped (low word count %d): %s", words, path)
                continue
            if suffix not in self._skip_structure_for:
                if not _has_structure_signals(text, self._min_signals):
                    log.debug("Skipped (low structure signals): %s", path)
                    continue
            eligible.append(path)
        log.info("VaultScanner: %d eligible files in %s", len(eligible), self._vault_path)
        return eligible

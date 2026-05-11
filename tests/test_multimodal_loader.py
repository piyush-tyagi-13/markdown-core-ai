from __future__ import annotations
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mdcore.config.models import VaultConfig


@pytest.fixture
def vault_cfg(tmp_path):
    return VaultConfig(path=str(tmp_path))


class TestMultiModalLoaderTxt:
    def test_load_txt_returns_document(self, vault_cfg, tmp_path):
        txt_file = tmp_path / "note.txt"
        txt_file.write_text("Hello world this is a plain text file.", encoding="utf-8")

        from mdcore.core.indexer.multimodal_loader import MultiModalLoader
        loader = MultiModalLoader(vault_cfg)
        doc = loader.load(txt_file)

        assert doc.page_content == "Hello world this is a plain text file."
        assert doc.metadata["filename"] == "note.txt"
        assert doc.metadata["file_type"] == "txt"
        assert doc.metadata["source_file"] == "note.txt"

    def test_load_txt_metadata_folder_path(self, vault_cfg, tmp_path):
        sub = tmp_path / "projects"
        sub.mkdir()
        txt_file = sub / "notes.txt"
        txt_file.write_text("Some content here.", encoding="utf-8")

        from mdcore.core.indexer.multimodal_loader import MultiModalLoader
        doc = MultiModalLoader(vault_cfg).load(txt_file)

        assert doc.metadata["folder_path"] == "projects"


class TestMultiModalLoaderPdf:
    def test_load_pdf_raises_import_error_without_pypdf(self, vault_cfg, tmp_path):
        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        import builtins
        real_import = builtins.__import__

        def block_pypdf(name, *args, **kwargs):
            if name == "pypdf":
                raise ImportError("no module named pypdf")
            return real_import(name, *args, **kwargs)

        from mdcore.core.indexer.multimodal_loader import MultiModalLoader
        loader = MultiModalLoader(vault_cfg)

        with patch("builtins.__import__", side_effect=block_pypdf):
            with pytest.raises(ImportError, match=r"markdowncore-ai\[multimodal\]"):
                loader._load_pdf(pdf_file)

    def test_load_pdf_returns_page_prefixed_text(self, vault_cfg, tmp_path):
        pdf_file = tmp_path / "report.pdf"
        pdf_file.write_bytes(b"%PDF fake")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Some PDF text."
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        from mdcore.core.indexer.multimodal_loader import MultiModalLoader
        loader = MultiModalLoader(vault_cfg)

        with patch.dict(sys.modules, {"pypdf": MagicMock(PdfReader=MagicMock(return_value=mock_reader))}):
            content = loader._load_pdf(pdf_file)

        assert "## Page 1" in content
        assert "Some PDF text." in content

    def test_load_pdf_metadata_file_type(self, vault_cfg, tmp_path):
        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(b"%PDF fake")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Text here."
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        from mdcore.core.indexer.multimodal_loader import MultiModalLoader
        loader = MultiModalLoader(vault_cfg)

        with patch.dict(sys.modules, {"pypdf": MagicMock(PdfReader=MagicMock(return_value=mock_reader))}):
            doc = loader.load(pdf_file)

        assert doc.metadata["file_type"] == "pdf"


class TestMultiModalLoaderDocx:
    def test_load_docx_raises_import_error_without_python_docx(self, vault_cfg, tmp_path):
        docx_file = tmp_path / "doc.docx"
        docx_file.write_bytes(b"PK fake docx")

        import builtins
        real_import = builtins.__import__

        def block_docx(name, *args, **kwargs):
            if name == "docx":
                raise ImportError("no module named docx")
            return real_import(name, *args, **kwargs)

        from mdcore.core.indexer.multimodal_loader import MultiModalLoader
        loader = MultiModalLoader(vault_cfg)

        with patch("builtins.__import__", side_effect=block_docx):
            with pytest.raises(ImportError, match=r"markdowncore-ai\[multimodal\]"):
                loader._load_docx(docx_file)

    def test_load_docx_heading_conversion(self, vault_cfg, tmp_path):
        docx_file = tmp_path / "doc.docx"
        docx_file.write_bytes(b"PK fake")

        mock_para_h2 = MagicMock()
        mock_para_h2.text = "Section Title"
        mock_para_h2.style.name = "Heading 2"

        mock_para_body = MagicMock()
        mock_para_body.text = "Body text here."
        mock_para_body.style.name = "Normal"

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para_h2, mock_para_body]
        mock_doc.tables = []

        from mdcore.core.indexer.multimodal_loader import MultiModalLoader
        loader = MultiModalLoader(vault_cfg)

        with patch.dict(sys.modules, {"docx": MagicMock(Document=MagicMock(return_value=mock_doc))}):
            content = loader._load_docx(docx_file)

        assert "## Section Title" in content
        assert "Body text here." in content

    def test_unsupported_format_raises(self, vault_cfg, tmp_path):
        bad_file = tmp_path / "file.xyz"
        bad_file.write_text("content")

        from mdcore.core.indexer.multimodal_loader import MultiModalLoader
        with pytest.raises(ValueError, match="unsupported format"):
            MultiModalLoader(vault_cfg).load(bad_file)

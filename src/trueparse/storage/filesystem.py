from __future__ import annotations
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from trueparse.core.errors import PDFEngineError
from trueparse.core.enums import ErrorCode
from trueparse.core.models import Asset, Document
from trueparse.pdf.images import ExtractedImageAsset


class FileSystemStorage:
    """Manages secure, atomic local filesystem storage for document parsing artifacts."""

    def __init__(self, root_path: str | Path = "data/output"):
        self.root_path = Path(root_path).resolve()
        self.root_path.mkdir(parents=True, exist_ok=True)

    def get_document_dir(self, document_id: str) -> Path:
        # Sanitize document_id against path traversal
        clean_id = "".join(c for c in document_id if c.isalnum() or c in ("-", "_"))
        if not clean_id:
            clean_id = "doc_default"
        doc_dir = self.root_path / clean_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        return doc_dir

    def prepare_directory_structure(self, document_id: str) -> dict[str, Path]:
        doc_dir = self.get_document_dir(document_id)
        dirs = {
            "root": doc_dir,
            "source": doc_dir / "source",
            "assets": doc_dir / "assets",
            "images": doc_dir / "assets" / "images",
            "figures": doc_dir / "assets" / "figures",
            "charts": doc_dir / "assets" / "charts",
            "diagrams": doc_dir / "assets" / "diagrams",
            "formulas": doc_dir / "assets" / "formulas",
            "output": doc_dir / "output",
            "intermediate": doc_dir / "intermediate",
            "debug": doc_dir / "debug",
            "debug_pages": doc_dir / "debug" / "pages",
        }
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        return dirs

    def save_source_pdf(
        self,
        source_path: Path,
        document_id: str,
        original_filename: Optional[str] = None,
    ) -> Path:
        dirs = self.prepare_directory_structure(document_id)
        source_filename = original_filename or (source_path.name if source_path.name else "original.pdf")
        # Sanitize filename
        clean_filename = Path(source_filename).name
        target = dirs["source"] / clean_filename
        if not target.exists():
            shutil.copy2(source_path, target)
        return target

    def save_image_asset(
        self,
        extracted: ExtractedImageAsset,
        document_id: str,
        subfolder: str = "images"
    ) -> Path:
        dirs = self.prepare_directory_structure(document_id)
        target_dir = dirs["assets"] / subfolder
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{extracted.asset_id}.{extracted.ext}"

        if not target_file.exists():
            # Atomic write
            with tempfile.NamedTemporaryFile(dir=target_dir, delete=False) as tmp:
                tmp.write(extracted.image_bytes)
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, target_file)

        return target_file

    def save_document_json(self, doc_json_str: str, document_id: str) -> Path:
        dirs = self.prepare_directory_structure(document_id)
        target_file = dirs["output"] / "document.json"

        # Atomic write with concurrency safety
        with tempfile.NamedTemporaryFile("w", dir=dirs["output"], encoding="utf-8", delete=False) as tmp:
            tmp.write(doc_json_str)
            tmp_path = Path(tmp.name)
        
        for attempt in range(3):
            try:
                os.replace(tmp_path, target_file)
                break
            except PermissionError:
                time.sleep(0.1 * (attempt + 1))
            except Exception:
                break
        
        if tmp_path.exists():
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        return target_file

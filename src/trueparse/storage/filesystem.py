from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from trueparse.core.enums import ErrorCode
from trueparse.core.errors import PDFEngineError
from trueparse.core.security import contain_path, sanitize_identifier
from trueparse.pdf.images import ExtractedImageAsset


class FileSystemStorage:
    """Manages secure, atomic local filesystem storage for document parsing artifacts.

    Every path this class produces is forced back inside ``root_path``; callers
    may pass attacker-influenced document IDs and filenames.
    """

    def __init__(self, root_path: str | Path = "data/output"):
        self.root_path = Path(root_path).expanduser().resolve()
        self.root_path.mkdir(parents=True, exist_ok=True)

    def get_document_dir(self, document_id: str) -> Path:
        clean_id = sanitize_identifier(document_id, fallback="doc_default")
        doc_dir = contain_path(clean_id, self.root_path)
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
        original_filename: str | None = None,
    ) -> Path:
        dirs = self.prepare_directory_structure(document_id)
        source_filename = original_filename or (source_path.name if source_path.name else "original.pdf")
        # Strip any directory component a client may have embedded in the name.
        clean_filename = Path(source_filename).name or "original.pdf"
        target = contain_path(clean_filename, dirs["source"])
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
        clean_sub = sanitize_identifier(subfolder, fallback="images")
        clean_ext = sanitize_identifier(extracted.ext, fallback="png")
        clean_asset = sanitize_identifier(extracted.asset_id, fallback="asset")
        target_dir = contain_path(clean_sub, dirs["assets"])
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{clean_asset}.{clean_ext}"

        if not target_file.exists():
            # Atomic write
            with tempfile.NamedTemporaryFile(dir=target_dir, delete=False) as tmp:
                tmp.write(extracted.image_bytes)
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, target_file)

        return target_file

    def save_document_json(self, doc_json_str: str, document_id: str) -> Path:
        dirs = self.prepare_directory_structure(document_id)
        return self.save_text(doc_json_str, dirs["output"], "document.json")

    def save_text(self, content: str, target_dir: Path, filename: str) -> Path:
        """Atomically writes ``content`` to ``target_dir/filename``.

        Windows raises ``PermissionError`` from ``os.replace`` when another
        handle (an antivirus scanner, an editor) still holds the destination,
        so the swap is retried with a short backoff before giving up.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = contain_path(Path(filename).name, target_dir)

        with tempfile.NamedTemporaryFile(
            "w", dir=target_dir, encoding="utf-8", delete=False, newline="\n"
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        last_error: BaseException | None = None
        for attempt in range(3):
            try:
                os.replace(tmp_path, target_file)
                return target_file
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.1 * (attempt + 1))
            except OSError as exc:
                last_error = exc
                break

        if tmp_path.exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        raise PDFEngineError(
            code=ErrorCode.ASSET_STORAGE_ERROR,
            message=f"Failed to write {target_file}: {last_error}",
            retryable=True,
        )

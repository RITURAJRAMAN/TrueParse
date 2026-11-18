from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from trueparse.exceptions import PDFEngineError
from trueparse.extraction.pdf.images import ExtractedImageAsset
from trueparse.io.paths import contain_path, sanitize_identifier
from trueparse.models.enums import ErrorCode


class FileSystemStorage:
    """Atomic, contained local artifact storage."""

    def __init__(self, root_path: str | Path = "data/output"):
        self.root_path = Path(root_path).expanduser().resolve()

    def get_document_dir(self, document_id: str) -> Path:
        clean_id = sanitize_identifier(document_id, fallback="doc_default")
        doc_dir = contain_path(clean_id, self.root_path)
        return doc_dir

    def save_image_asset(
        self,
        extracted: ExtractedImageAsset,
        document_id: str,
        subfolder: str = "images",
    ) -> Path:
        """Saves an image asset into assets/{subfolder}/{asset_id}.{ext}, creating dirs on demand."""
        doc_dir = self.get_document_dir(document_id)
        assets_dir = contain_path("assets", doc_dir)
        clean_sub = sanitize_identifier(subfolder, fallback="images")
        clean_ext = sanitize_identifier(extracted.ext, fallback="png")
        clean_asset = sanitize_identifier(extracted.asset_id, fallback="asset")

        target_dir = contain_path(clean_sub, assets_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{clean_asset}.{clean_ext}"

        if not target_file.exists():
            # Atomic write
            with tempfile.NamedTemporaryFile(dir=target_dir, delete=False) as tmp:
                tmp.write(extracted.image_bytes)
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, target_file)

        return target_file

    def save_deliverable(
        self,
        content: str,
        document_id: str,
        filename: str,
    ) -> Path:
        """Atomically writes a deliverable directly into the document directory."""
        doc_dir = self.get_document_dir(document_id)
        return self.save_text(content, doc_dir, filename)

    def save_text(self, content: str, target_dir: Path, filename: str) -> Path:
        """Atomically writes ``content`` to ``target_dir/filename``."""
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

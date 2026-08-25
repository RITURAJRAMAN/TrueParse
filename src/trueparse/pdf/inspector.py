from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
from pydantic import BaseModel, Field

from trueparse.core.enums import ErrorCode
from trueparse.core.errors import PDFEngineError
from trueparse.core.models import DocumentMetadata


class PageForensics(BaseModel):
    page_number: int
    width: float
    height: float
    rotation: int = 0
    text_length: int = 0
    word_count: int = 0
    line_count: int = 0
    embedded_images: int = 0
    drawing_count: int = 0
    has_native_text: bool = False
    likely_scan: bool = False
    fonts: list[str] = Field(default_factory=list)


class DocumentInspection(BaseModel):
    document_id: str
    file_path: str
    file_size_bytes: int
    sha256: str
    page_count: int
    is_encrypted: bool
    metadata: DocumentMetadata
    pages: list[PageForensics] = Field(default_factory=list)
    overall_native_text: bool = True
    overall_likely_scan: bool = False


class PDFInspector:
    """Performs forensic inspection of a PDF file using PyMuPDF."""

    @staticmethod
    def calculate_sha256(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    @classmethod
    def inspect(
        cls,
        file_path: str | Path,
        max_file_size_mb: int = 200,
        password: str | None = None,
    ) -> DocumentInspection:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise PDFEngineError(
                code=ErrorCode.INVALID_PDF,
                message=f"File not found: {path}",
            )

        file_size = path.stat().st_size
        if file_size > max_file_size_mb * 1024 * 1024:
            raise PDFEngineError(
                code=ErrorCode.PDF_RESOURCE_LIMIT,
                message=f"File size ({file_size / (1024*1024):.1f} MB) exceeds maximum allowed limit ({max_file_size_mb} MB).",
            )

        sha256 = cls.calculate_sha256(path)
        doc_id = f"doc_{sha256[:12]}"

        try:
            doc = fitz.open(path)
        except Exception as e:
            raise PDFEngineError(
                code=ErrorCode.INVALID_PDF,
                message=f"Failed to open PDF: {e}",
            ) from e

        was_encrypted = bool(doc.is_encrypted)
        if was_encrypted:
            # An empty owner password unlocks many "protected" PDFs that only
            # restrict printing, so try that before demanding one from the user.
            if not doc.authenticate(password or ""):
                doc.close()
                raise PDFEngineError(
                    code=(
                        ErrorCode.PDF_PASSWORD_INCORRECT
                        if password
                        else ErrorCode.PDF_PASSWORD_REQUIRED
                    ),
                    message=(
                        "Incorrect password for encrypted PDF."
                        if password
                        else "PDF is encrypted. Supply a password via ParseOptions(password=...)."
                    ),
                    document_id=doc_id,
                )

        raw_meta = doc.metadata or {}
        meta = DocumentMetadata(
            title=raw_meta.get("title"),
            author=raw_meta.get("author"),
            creator=raw_meta.get("creator"),
            producer=raw_meta.get("producer"),
            creation_date=raw_meta.get("creationDate"),
            modification_date=raw_meta.get("modDate"),
            page_count=len(doc),
            file_size_bytes=file_size,
            sha256=sha256,
        )

        pages_forensics: list[PageForensics] = []
        scan_pages = 0

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            rect = page.rect
            text = page.get_text("text") or ""
            text_length = len(text.strip())
            words = page.get_text("words") or []
            word_count = len(words)
            images = page.get_images() or []
            drawings = page.get_drawings() or []
            fonts_list = [f[3] for f in page.get_fonts() if len(f) > 3]

            has_native_text = text_length > 20
            # Likely scan if text is very low but images exist covering a large area or single large image
            likely_scan = (text_length < 30) and (len(images) > 0)

            if likely_scan:
                scan_pages += 1

            pages_forensics.append(
                PageForensics(
                    page_number=page_idx + 1,
                    width=rect.width,
                    height=rect.height,
                    rotation=page.rotation,
                    text_length=text_length,
                    word_count=word_count,
                    line_count=len(text.splitlines()),
                    embedded_images=len(images),
                    drawing_count=len(drawings),
                    has_native_text=has_native_text,
                    likely_scan=likely_scan,
                    fonts=list(set(fonts_list)),
                )
            )

        overall_likely_scan = (scan_pages / max(1, len(doc))) > 0.5
        overall_native_text = not overall_likely_scan

        doc.close()

        return DocumentInspection(
            document_id=doc_id,
            file_path=str(path.resolve()),
            file_size_bytes=file_size,
            sha256=sha256,
            page_count=len(pages_forensics),
            is_encrypted=was_encrypted,
            metadata=meta,
            pages=pages_forensics,
            overall_native_text=overall_native_text,
            overall_likely_scan=overall_likely_scan,
        )

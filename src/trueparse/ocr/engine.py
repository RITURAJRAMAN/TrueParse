"""Local OCR backends.

Two RapidOCR generations are supported: the modern ``rapidocr`` package
(Python 3.13+) and the legacy ``rapidocr_onnxruntime``. Both are pure wheels,
which preserves TrueParse's "pip install and go, zero cloud" promise. Neither
is required: without one, OCR silently no-ops.
"""
from __future__ import annotations

import importlib
import importlib.util
import io
import logging
from typing import Any

from pydantic import BaseModel, Field

from trueparse.core.models import BoundingBox

logger = logging.getLogger("trueparse")

_ENGINE: Any = None
_ENGINE_KIND: str | None = None
_ENGINE_TRIED = False

_MODERN = "rapidocr"
_LEGACY = "rapidocr_onnxruntime"


class OCRLine(BaseModel):
    """One recognised text line with its geometry and model confidence."""
    text: str
    bbox: BoundingBox
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OCRPageResult(BaseModel):
    """Everything OCR recovered from a single page raster."""
    lines: list[OCRLine] = Field(default_factory=list)
    mean_confidence: float = 0.0
    engine: str = "rapidocr"
    engine_version: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def _installed_backend() -> str | None:
    for name in (_MODERN, _LEGACY):
        if importlib.util.find_spec(name) is not None:
            return name
    return None


def ocr_available() -> bool:
    """True when an OCR backend can be imported."""
    return _installed_backend() is not None


def _engine_version(package: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return None


def _load_engine() -> tuple[Any, str | None]:
    """Loads and caches the OCR engine. Model init costs ~1s, so cache it."""
    global _ENGINE, _ENGINE_KIND, _ENGINE_TRIED
    if _ENGINE is not None:
        return _ENGINE, _ENGINE_KIND
    if _ENGINE_TRIED:
        return None, None

    _ENGINE_TRIED = True
    backend = _installed_backend()
    if backend is None:
        logger.warning(
            "OCR requested but no backend is installed. "
            "Install the optional extra with: pip install trueparse[ocr]"
        )
        return None, None

    # RapidOCR chats at INFO about every model file it loads.
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    try:
        module = importlib.import_module(backend)
        _ENGINE = module.RapidOCR()
        _ENGINE_KIND = backend
        version = _engine_version(backend) or "unknown"
        logger.info(f"OCR backend initialised: {backend} {version}")
    except Exception as exc:
        logger.error(f"Failed to initialise OCR engine '{backend}': {exc}")
        return None, None

    return _ENGINE, _ENGINE_KIND


def _normalise(raw: Any, kind: str) -> list[tuple[Any, str, Any]]:
    """Flattens either backend's output into (box, text, score) triples."""
    if raw is None:
        return []

    if kind == _MODERN:
        boxes = getattr(raw, "boxes", None)
        txts = getattr(raw, "txts", None)
        scores = getattr(raw, "scores", None)
        if boxes is None or txts is None:
            return []
        if scores is None:
            scores = [0.0] * len(txts)
        # Backends can disagree on list lengths; pair what is available.
        return list(zip(boxes, txts, scores, strict=False))

    # Legacy yields a list of [box, text, score].
    return [(entry[0], entry[1], entry[2]) for entry in raw if len(entry) >= 3]


class OCREngine:
    """Runs OCR over rasterised pages and maps results back to PDF space."""

    @classmethod
    def is_available(cls) -> bool:
        """Cheap importability probe; does not initialise the models."""
        return ocr_available()

    @classmethod
    def recognize(
        cls,
        image_bytes: bytes,
        scale: float = 1.0,
        origin: tuple[float, float] = (0.0, 0.0),
        min_confidence: float = 0.3,
    ) -> OCRPageResult:
        """Recognises text in a rendered image.

        Args:
            image_bytes: Encoded PNG/JPEG bytes of the rendered page or region.
            scale: Render zoom that produced the image (dpi / 72). Boxes are
                divided by this to return to PDF points.
            origin: PDF-space top-left of the region, added back to every box.
            min_confidence: Lines scoring below this are discarded.

        Returns:
            An :class:`OCRPageResult`; empty when no backend is installed.
        """
        engine, kind = _load_engine()
        if engine is None or kind is None:
            return OCRPageResult(lines=[], mean_confidence=0.0, engine="unavailable")

        try:
            import numpy as np
            from PIL import Image

            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            raw = engine(np.array(image))
            if isinstance(raw, tuple):
                raw = raw[0]
        except Exception as exc:
            logger.error(f"OCR recognition failed: {exc}")
            return OCRPageResult(lines=[], mean_confidence=0.0, engine=kind)

        safe_scale = scale if scale > 0 else 1.0
        off_x, off_y = origin
        lines: list[OCRLine] = []

        for box, text, score in _normalise(raw, kind):
            text = (text or "").strip()
            if not text:
                continue
            try:
                confidence = float(score)
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < min_confidence:
                continue

            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            lines.append(
                OCRLine(
                    text=text,
                    bbox=BoundingBox(
                        x0=min(xs) / safe_scale + off_x,
                        y0=min(ys) / safe_scale + off_y,
                        x1=max(xs) / safe_scale + off_x,
                        y1=max(ys) / safe_scale + off_y,
                    ),
                    confidence=min(1.0, max(0.0, confidence)),
                )
            )

        # Detection order follows confidence, not layout.
        lines.sort(key=lambda ln: (round(ln.bbox.y0, 1), ln.bbox.x0))

        mean_conf = sum(ln.confidence for ln in lines) / len(lines) if lines else 0.0
        return OCRPageResult(
            lines=lines,
            mean_confidence=round(mean_conf, 4),
            engine=kind,
            engine_version=_engine_version(kind),
        )

    @classmethod
    def group_into_blocks(
        cls,
        result: OCRPageResult,
        line_gap_tolerance: float = 1.6,
    ) -> list[tuple[str, BoundingBox, float]]:
        """Groups adjacent OCR lines into paragraph-like blocks.

        Lines merge while the vertical gap stays within ``line_gap_tolerance``
        times the median line height, matching the block granularity of native
        extraction.

        Returns:
            ``(text, bbox, mean_confidence)`` tuples.
        """
        if not result.lines:
            return []

        heights = sorted(ln.bbox.height for ln in result.lines if ln.bbox.height > 0)
        median_h = heights[len(heights) // 2] if heights else 10.0

        blocks: list[tuple[str, BoundingBox, float]] = []
        current: list[OCRLine] = [result.lines[0]]

        for line in result.lines[1:]:
            if line.bbox.y0 - current[-1].bbox.y1 > median_h * line_gap_tolerance:
                blocks.append(cls._collapse(current))
                current = [line]
            else:
                current.append(line)

        blocks.append(cls._collapse(current))
        return blocks

    @staticmethod
    def _collapse(lines: list[OCRLine]) -> tuple[str, BoundingBox, float]:
        text = "\n".join(ln.text for ln in lines)
        bbox = BoundingBox(
            x0=min(ln.bbox.x0 for ln in lines),
            y0=min(ln.bbox.y0 for ln in lines),
            x1=max(ln.bbox.x1 for ln in lines),
            y1=max(ln.bbox.y1 for ln in lines),
        )
        conf = sum(ln.confidence for ln in lines) / len(lines)
        return text, bbox, round(conf, 4)

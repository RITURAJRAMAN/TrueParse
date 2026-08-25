"""Filesystem containment helpers.

TrueParse ships a network service, so any path that originates from a request
must be proven to stay inside an allow-listed root before it is opened or
written to. These helpers centralise that check so route handlers cannot forget
it.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from trueparse.core.enums import ErrorCode
from trueparse.core.errors import PDFEngineError

#: The only directory TrueParse may write parsing output into.
ENV_OUTPUT_ROOT = "TRUEPARSE_OUTPUT_ROOT"

#: Optional API key; when set, parsing endpoints require X-API-Key.
ENV_API_KEY = "TRUEPARSE_API_KEY"

#: Comma-separated CORS origins. Empty (the default) disables CORS entirely.
ENV_CORS_ORIGINS = "TRUEPARSE_CORS_ORIGINS"

#: Hard ceiling on a single upload, in megabytes.
ENV_MAX_UPLOAD_MB = "TRUEPARSE_MAX_UPLOAD_MB"

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


def resolved_output_root() -> Path:
    """The one directory this process may write parsing artifacts into."""
    raw = os.environ.get(ENV_OUTPUT_ROOT) or "data/output"
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_identifier(value: str, fallback: str = "unknown") -> str:
    """Strips every character that could influence path resolution.

    Used for ``document_id`` and ``asset_id`` path segments, which are attacker
    controlled on the read endpoints.
    """
    clean = _SAFE_ID_RE.sub("", value or "")
    return clean or fallback


def contain_path(candidate: str | Path, root: str | Path) -> Path:
    """Resolves ``candidate`` and asserts it lives inside ``root``.

    Raises:
        PDFEngineError: if the resolved path escapes ``root`` (via ``..``,
            an absolute path, a symlink, or a Windows drive change).
    """
    root_resolved = Path(root).expanduser().resolve()
    target = Path(candidate).expanduser()
    if not target.is_absolute():
        target = root_resolved / target
    target = target.resolve()

    if target != root_resolved and root_resolved not in target.parents:
        raise PDFEngineError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message=(
                "Resolved path escapes the permitted output root. "
                "Set TRUEPARSE_OUTPUT_ROOT to widen the allowed area."
            ),
            details={"root": str(root_resolved)},
        )
    return target


def api_key() -> str | None:
    """Returns the configured API key, or ``None`` when auth is disabled."""
    key = os.environ.get(ENV_API_KEY, "").strip()
    return key or None


def cors_origins() -> list[str]:
    """Returns the configured CORS origins (empty list disables CORS)."""
    raw = os.environ.get(ENV_CORS_ORIGINS, "").strip()
    if not raw:
        return []
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def max_upload_bytes(default_mb: int = 200) -> int:
    """Upload ceiling in bytes, enforced while streaming the request body."""
    raw = os.environ.get(ENV_MAX_UPLOAD_MB, "").strip()
    try:
        mb = int(raw) if raw else default_mb
    except ValueError:
        mb = default_mb
    return max(1, mb) * 1024 * 1024


def iter_existing(paths: Iterable[Path]) -> list[Path]:
    """Filters an iterable of paths down to those that exist."""
    return [p for p in paths if p.exists()]

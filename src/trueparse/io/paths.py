from __future__ import annotations

import re
from pathlib import Path

from trueparse.exceptions import PDFEngineError
from trueparse.models.enums import ErrorCode

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


def sanitize_identifier(value: str, fallback: str = "unknown") -> str:
    """Normalize identifiers used in local artifact paths."""
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
            message=("Resolved path escapes the destination directory."),
            details={"root": str(root_resolved)},
        )
    return target

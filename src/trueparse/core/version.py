"""Single source of truth for the engine version.

The version is declared once in ``pyproject.toml``. At runtime we read it back
from the installed distribution metadata so the package, the API server banner
and the serialized ``document.json`` can never drift apart.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

#: Fallback for an uninstalled source tree.
_FALLBACK_VERSION = "0.1.2"


def get_version() -> str:
    """Returns the installed TrueParse version."""
    try:
        return _dist_version("trueparse")
    except PackageNotFoundError:
        return _FALLBACK_VERSION


__version__ = get_version()

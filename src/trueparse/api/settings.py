from __future__ import annotations

import os

ENV_API_KEY = "TRUEPARSE_API_KEY"


ENV_CORS_ORIGINS = "TRUEPARSE_CORS_ORIGINS"


ENV_MAX_UPLOAD_MB = "TRUEPARSE_MAX_UPLOAD_MB"


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

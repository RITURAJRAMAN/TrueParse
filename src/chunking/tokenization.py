from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Literal

from trueparse.exceptions import PDFEngineError
from trueparse.models.enums import ErrorCode

TokenizerName = Literal["approximate", "cl100k_base", "o200k_base"]
_WORD_RE = re.compile(r"\S+")
_SPECS: dict[str, tuple[str, str, dict[str, int]]] = {
    "cl100k_base": (
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
        r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s""",
        {
            "<|endoftext|>": 100257,
            "<|fim_prefix|>": 100258,
            "<|fim_middle|>": 100259,
            "<|fim_suffix|>": 100260,
            "<|endofprompt|>": 100276,
        },
    ),
    "o200k_base": (
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
        "|".join(
            [
                r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
                r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
                r"\p{N}{1,3}",
                r" ?[^\s\p{L}\p{N}]+[\r\n/]*",
                r"\s*[\r\n]+",
                r"\s+(?!\S)",
                r"\s+",
            ]
        ),
        {"<|endoftext|>": 199999, "<|endofprompt|>": 200018},
    ),
}


def approximate_tokens(text: str) -> int:
    return int(len(_WORD_RE.findall(text)) * 1.3) + 1 if text else 0


def encoding_path(name: str = "cl100k_base") -> Path:
    if name not in _SPECS:
        raise PDFEngineError(ErrorCode.INVALID_OPTIONS, f"Unsupported tokenizer: {name}")
    explicit = os.environ.get("TRUEPARSE_TOKENIZER_FILE")
    if explicit:
        return Path(explicit).expanduser()
    directory = os.environ.get("TRUEPARSE_TOKENIZER_DIR")
    if directory:
        return Path(directory).expanduser() / f"{name}.tiktoken"
    cache = os.environ.get(
        "TIKTOKEN_CACHE_DIR",
        os.environ.get("DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache")),
    )
    url = f"https://openaipublic.blob.core.windows.net/encodings/{name}.tiktoken"
    return Path(cache) / hashlib.sha1(url.encode()).hexdigest()


@lru_cache(maxsize=4)
def _load_encoding(name: str, path: str, _modified_ns: int, _size: int):
    try:
        import tiktoken
    except ImportError as exc:
        raise PDFEngineError(
            ErrorCode.INVALID_OPTIONS, f"Install trueparse[tokenizer] for chunk_tokenizer={name!r}."
        ) from exc
    digest, pattern, special = _SPECS[name]
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise PDFEngineError(
            ErrorCode.INVALID_OPTIONS, f"The local {name} encoding file failed checksum validation."
        )
    ranks = {
        base64.b64decode(token): int(rank)
        for token, rank in (line.split() for line in raw.splitlines() if line)
    }
    return tiktoken.Encoding(name=name, pat_str=pattern, mergeable_ranks=ranks, special_tokens=special)


def token_counter(name: str = "approximate") -> Callable[[str], int]:
    if name == "approximate":
        return approximate_tokens
    path = encoding_path(name)
    if not path.is_file():
        raise PDFEngineError(
            ErrorCode.INVALID_OPTIONS,
            f"Provision {name} before parsing: install trueparse[tokenizer], then run "
            f"python -c \"import tiktoken; tiktoken.get_encoding('{name}')\" during setup. "
            "Alternatively set TRUEPARSE_TOKENIZER_FILE or TRUEPARSE_TOKENIZER_DIR. Parsing never downloads encoding data.",
        )
    stat = path.stat()
    try:
        encoding = _load_encoding(name, str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError as exc:
        raise PDFEngineError(ErrorCode.INVALID_OPTIONS, f"Cannot read local tokenizer data: {exc}") from exc
    return lambda text: len(encoding.encode(text, disallowed_special=()))

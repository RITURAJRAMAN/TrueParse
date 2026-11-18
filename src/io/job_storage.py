from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


class JobStorage:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("inputs", "results", "staging"):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)

    def path(self, kind: str, identifier: str) -> Path:
        if kind not in ("inputs", "results", "staging") or not re.fullmatch(r"[a-z0-9_-]+", identifier):
            raise ValueError("Invalid managed storage path")
        target = self.root / kind / identifier
        if (
            target.is_symlink()
            or target.parent.is_symlink()
            or not target.resolve().is_relative_to(self.root)
        ):
            raise ValueError("Managed storage path escapes root")
        return target

    def remove(self, kind: str, identifier: str) -> None:
        path = self.path(kind, identifier)
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_symlink() or not child.resolve().is_relative_to(self.root):
                    raise ValueError("Unsafe staging directory")
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    @staticmethod
    def sync(stream):
        stream.flush()
        os.fsync(stream.fileno())

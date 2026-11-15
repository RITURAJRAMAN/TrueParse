from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import Field, PrivateAttr

from trueparse.contracts.document import CanonicalDocument, Issue, RepresentationReport, Scope
from trueparse.contracts.response import ParseResponse
from trueparse.exporters.files import serialize_outputs
from trueparse.io.paths import contain_path


class ParseResult(ParseResponse):
    """Requested deliverables and actionable warnings, independent of storage."""

    source_file: str
    warnings: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    scope: Scope
    representations: dict[str, RepresentationReport] = Field(default_factory=dict)
    _asset_bytes: dict[str, bytes] = PrivateAttr(default_factory=dict)
    _asset_paths: dict[str, str] = PrivateAttr(default_factory=dict)
    _response_bytes: int = PrivateAttr(default=0)

    @property
    def markdown(self) -> str | None:
        return self.data.markdown

    @property
    def text(self) -> str | None:
        return self.data.text

    @property
    def html(self) -> str | None:
        return self.data.html

    @property
    def document(self) -> dict[str, Any] | None:
        return self.data.document

    @property
    def chunks(self) -> list[dict[str, Any]] | None:
        return self.data.chunks

    @property
    def page_count(self) -> int:
        return int(self.metrics.get("page_count", 0))

    def iter_nodes(self):
        if self.document is None:
            raise ValueError("Request the JSON format to traverse document nodes.")
        return CanonicalDocument.model_validate(self.document).iter_nodes()

    def get_asset(self, asset_id: str) -> bytes:
        """Read an extracted asset without depending on a filesystem path."""
        if asset_id in self._asset_bytes:
            return self._asset_bytes[asset_id]
        asset = ((self.document or {}).get("assets") or {}).get(asset_id, {})
        if asset.get("data_uri"):
            return base64.b64decode(asset["data_uri"].split(",", 1)[1], validate=True)
        raise KeyError(f"Asset {asset_id!r} is unavailable; request images='base64' and JSON over HTTP.")

    def save(self, directory: str | Path) -> Path:
        """Save a clean export into a new or empty directory.

        Refuse nonempty destinations so earlier user files are never mixed with
        this result or silently deleted. Build in a sibling staging directory
        before making the completed export visible.
        """
        target = Path(directory).expanduser().absolute()
        if target.is_symlink():
            raise ValueError("The export directory must not be a symbolic link.")
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise FileExistsError(f"{target} is not empty. Choose a new or empty export directory.")
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
        stem = Path(self.source_file.replace("\\", "/")).stem or "document"
        try:
            for extension, content in serialize_outputs(self.data, self.formats_provided):
                contain_path(f"{stem}.{extension}", stage).write_text(content, encoding="utf-8")
            asset_paths = dict(self._asset_paths)
            asset_paths.update(
                {
                    key: asset["path"]
                    for key, asset in ((self.document or {}).get("assets") or {}).items()
                    if asset.get("path")
                }
            )
            for asset_id, relative_path in asset_paths.items():
                if asset_id not in self._asset_bytes:
                    raise ValueError(
                        f"Cannot save linked asset {asset_id}: binary data is missing. Use base64 for serialized results."
                    )
                asset_target = contain_path(relative_path, stage)
                asset_target.parent.mkdir(parents=True, exist_ok=True)
                asset_target.write_bytes(self._asset_bytes[asset_id])
            if target.exists():
                target.rmdir()  # only the empty directory accepted above
            os.rename(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return target

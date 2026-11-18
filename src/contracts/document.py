from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Issue(Contract):
    code: str
    stage: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    page: int | None = Field(default=None, ge=1)
    node_ids: list[str] = Field(default_factory=list)
    recovered: bool = False


def extraction_status(issues: list[Issue], failed_pages: list[int]) -> Literal["completed", "partial"]:
    return (
        "partial"
        if failed_pages or any(not issue.recovered and issue.severity != "info" for issue in issues)
        else "completed"
    )


class Source(Contract):
    filename: str | None = None
    sha256: str
    page_count: int = Field(ge=0)


class Scope(Contract):
    source_pages: int = Field(ge=0)
    requested_pages: list[int]
    processed_pages: list[int]
    failed_pages: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self):
        for pages in (self.requested_pages, self.processed_pages, self.failed_pages):
            if len(set(pages)) != len(pages) or any(p < 1 or p > self.source_pages for p in pages):
                raise ValueError("Invalid page scope")
        requested = set(self.requested_pages)
        processed, failed = set(self.processed_pages), set(self.failed_pages)
        if processed & failed or processed | failed != requested:
            raise ValueError("Processed and failed pages must partition requested pages")
        return self


class Fragment(Contract):
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float] | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    source_id: str | None = None

    @model_validator(mode="after")
    def validate_fragment(self):
        if (self.start is None) != (self.end is None) or (
            self.start is not None and self.end is not None and self.start > self.end
        ):
            raise ValueError("Invalid source offsets")
        if self.bbox and (self.bbox[0] > self.bbox[2] or self.bbox[1] > self.bbox[3]):
            raise ValueError("Invalid bounding box")
        return self


class Node(Contract):
    id: str
    role: str
    content: str
    source: list[Fragment]
    method: str
    engine: str


class TextNode(Node):
    type: Literal["text"] = "text"
    level: int | None = Field(default=None, ge=1)
    original_text: str | None = None
    source_texts: dict[str, str] | None = None
    normalization_map: list[TextMapping] | None = None
    recovery_reason: str | None = None

    @model_validator(mode="after")
    def validate_normalization(self):
        if self.normalization_map is None:
            return self
        cursor = 0
        for span in self.normalization_map:
            if span.start != cursor or span.end <= span.start or span.end > len(self.content):
                raise ValueError("Normalization spans must partition the content")
            if span.source_id is not None:
                original = (self.source_texts or {}).get(span.source_id)
                if (
                    original is None
                    or span.source_start is None
                    or span.source_end is None
                    or not 0 <= span.source_start <= span.source_end <= len(original)
                ):
                    raise ValueError("Invalid raw source span")
                if self.content[span.start : span.end] != original[span.source_start : span.source_end]:
                    raise ValueError("Normalization copy span does not match source text")
            cursor = span.end
        if cursor != len(self.content):
            raise ValueError("Normalization spans do not cover the content")
        return self


class TextMapping(Contract):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    source_id: str | None = None
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, ge=0)


class ListItem(Contract):
    text: str
    marker: str | None = None
    children: list[ListItem] = Field(default_factory=list)


class ListNode(Node):
    type: Literal["list"] = "list"
    ordered: bool
    items: list[ListItem]


class Cell(Contract):
    id: str
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    is_header: bool = False
    text: str
    kind: Literal["text", "visual_marker"] = "text"
    source: Fragment


class TableNode(Node):
    type: Literal["table"] = "table"
    rows: int = Field(ge=0)
    columns: int = Field(ge=0)
    cells: list[Cell] | None = None
    markdown: str | None = None
    html: str | None = None

    @model_validator(mode="after")
    def validate_cells(self):
        if sum(cell.row_span * cell.col_span for cell in self.cells or []) > 1_000_000:
            raise ValueError("Table occupancy exceeds the validation budget")
        occupied: set[tuple[int, int]] = set()
        ids: set[str] = set()
        for cell in self.cells or []:
            if cell.id in ids:
                raise ValueError("Duplicate table cell ID")
            ids.add(cell.id)
            if cell.row + cell.row_span > self.rows or cell.column + cell.col_span > self.columns:
                raise ValueError("Table cell exceeds declared dimensions")
            for row in range(cell.row, cell.row + cell.row_span):
                for col in range(cell.column, cell.column + cell.col_span):
                    if (row, col) in occupied:
                        raise ValueError("Overlapping table cells")
                    occupied.add((row, col))
        return self


class VisualNode(Node):
    type: Literal["visual"] = "visual"
    asset_id: str | None = None
    parent_table_id: str | None = None
    availability: Literal["available", "omitted", "unavailable"]


ContentNode = Annotated[TextNode | ListNode | TableNode | VisualNode, Field(discriminator="type")]


class Asset(Contract):
    id: str
    mime_type: str
    sha256: str | None = None
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    availability: Literal["available", "omitted", "unavailable"]
    path: str | None = None
    data_uri: str | None = None

    @model_validator(mode="after")
    def validate_delivery(self):
        if self.availability == "available":
            if bool(self.path) == bool(self.data_uri):
                raise ValueError("Available asset requires exactly one delivery representation")
        elif self.path or self.data_uri:
            raise ValueError("Omitted or unavailable asset cannot carry a payload")
        return self


class Page(Contract):
    page_number: int = Field(ge=1)
    width: float = Field(ge=0)
    height: float = Field(ge=0)
    rotation: int
    node_ids: list[str]


class Relation(Contract):
    id: str
    type: str
    source_id: str
    target_id: str


class Section(Contract):
    id: str
    title: str
    level: int = Field(ge=0)
    parent_id: str | None = None
    node_ids: list[str]


class Projection(Contract):
    excluded_node_ids: list[str] = Field(default_factory=list)
    omitted_fields: list[str] = Field(default_factory=list)


class RepresentationReport(Contract):
    rendered_node_ids: list[str]
    excluded_node_ids: list[str] = Field(default_factory=list)
    losses: list[str] = Field(default_factory=list)


class CanonicalDocument(Contract):
    schema_version: Literal["3.0"] = "3.0"
    document_id: str
    extraction_id: str
    engine_version: str
    source: Source
    scope: Scope
    status: Literal["completed", "partial"]
    metadata: dict[str, str | int | None] | None = None
    pages: list[Page]
    nodes: dict[str, ContentNode]
    reading_order: list[str]
    assets: dict[str, Asset]
    sections: list[Section] | None = None
    relationships: list[Relation] | None = None
    issues: list[Issue] = Field(default_factory=list)
    projection: Projection = Field(default_factory=Projection)
    diagnostics: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_references(self):
        if self.source.page_count != self.scope.source_pages:
            raise ValueError("Source page count does not match scope")
        if self.status != extraction_status(self.issues, self.scope.failed_pages):
            raise ValueError("Document status does not match extraction issues")
        node_ids = set(self.nodes)
        if set(self.reading_order) != node_ids or len(self.reading_order) != len(node_ids):
            raise ValueError("Reading order must contain every node exactly once")
        page_ids = {page.page_number for page in self.pages}
        if len(page_ids) != len(self.pages) or page_ids != set(self.scope.requested_pages):
            raise ValueError("Pages must match requested scope")
        for page in self.pages:
            if len(set(page.node_ids)) != len(page.node_ids) or not set(page.node_ids) <= node_ids:
                raise ValueError("Invalid page node references")
        placements = {p.page_number: set(p.node_ids) for p in self.pages}
        sources = {key: {fragment.page for fragment in node.source} for key, node in self.nodes.items()}
        if any(page.page_number not in sources[key] for page in self.pages for key in page.node_ids):
            raise ValueError("Page placement has no matching source fragment")
        for key, node in self.nodes.items():
            if key != node.id or not node.source:
                raise ValueError("Node requires matching ID and source fragments")
            for fragment in node.source:
                if fragment.page not in page_ids or key not in placements[fragment.page]:
                    raise ValueError("Source fragments must resolve to page placements")
                if fragment.end is not None and fragment.end > len(node.content):
                    raise ValueError("Source offset exceeds content")
            if isinstance(node, TableNode):
                if any(c.source.page not in sources[key] for c in node.cells or []):
                    raise ValueError("Table cell source page is unavailable")
            if isinstance(node, VisualNode) and node.availability == "available" and not node.asset_id:
                raise ValueError("Available visual requires an asset reference")
            if isinstance(node, VisualNode) and node.asset_id:
                asset = self.assets.get(node.asset_id)
                if asset is None or asset.availability != node.availability:
                    raise ValueError("Visual asset reference or availability is invalid")
            if isinstance(node, VisualNode) and node.parent_table_id is not None:
                if not isinstance(self.nodes.get(node.parent_table_id), TableNode):
                    raise ValueError("Visual parent table reference is invalid")
        section_index = {section.id: section for section in self.sections or []}
        if len(section_index) != len(self.sections or []) or node_ids & set(section_index):
            raise ValueError("Duplicate section IDs")
        for section in section_index.values():
            if not set(section.node_ids) <= node_ids:
                raise ValueError("Unresolved section node reference")
            seen = {section.id}
            parent = section.parent_id
            while parent is not None:
                if parent in seen or parent not in section_index:
                    raise ValueError("Invalid section ancestry")
                seen.add(parent)
                parent = section_index[parent].parent_id
        targets = node_ids | set(section_index)
        relationship_ids: set[str] = set()
        for relation in self.relationships or []:
            if (
                relation.id in relationship_ids
                or relation.source_id not in targets
                or relation.target_id not in targets
            ):
                raise ValueError("Invalid relationship")
            relationship_ids.add(relation.id)
        if any(key != asset.id for key, asset in self.assets.items()):
            raise ValueError("Asset key must match its ID")
        return self

    def iter_nodes(self) -> Iterator[ContentNode]:
        return (self.nodes[node_id] for node_id in self.reading_order)

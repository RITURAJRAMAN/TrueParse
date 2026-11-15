from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from trueparse.config.options import JsonOptions
from trueparse.contracts.document import (
    Asset,
    CanonicalDocument,
    Cell,
    ContentNode,
    Fragment,
    ListNode,
    Page,
    Projection,
    Relation,
    Section,
    Source,
    TableNode,
    TextNode,
    VisualNode,
    extraction_status,
)
from trueparse.models.document import Document
from trueparse.models.elements import TableElement
from trueparse.models.enums import VISUAL_TYPES, ElementType, ImageInclusionMode
from trueparse.models.geometry import BoundingBox
from trueparse.processing.lists import ordered, parse_list


def _bounds(bbox: BoundingBox | None) -> tuple[float, float, float, float] | None:
    return (bbox.x0, bbox.y0, bbox.x1, bbox.y1) if bbox else None


def _fragments(element, coordinates):
    segments = element.metadata.get("source_segments")
    if segments and not isinstance(element, TableElement):
        return [
            Fragment(
                page=segment["page"],
                bbox=segment.get("bbox") if coordinates else None,
                start=segment["start"],
                end=segment["end"],
                source_id=segment["element_id"],
            )
            for segment in segments
        ]
    continuation_bounds = {
        item["page"]: item.get("bbox") for item in element.metadata.get("continuation_bboxes", [])
    }
    pages = sorted(
        {
            element.page,
            *continuation_bounds,
            *(cell.page or element.page for cell in getattr(element, "cells", [])),
        }
    )
    return [
        Fragment(
            page=page,
            bbox=(_bounds(element.bbox) if page == element.page else continuation_bounds.get(page))
            if coordinates
            else None,
            source_id=element.id,
        )
        for page in pages
    ]


def project_document(document: Document, options: JsonOptions) -> CanonicalDocument:
    nodes: dict[str, ContentNode] = {}
    node: ContentNode
    order = []
    excluded = []
    used_assets = set()
    for page in document.pages:
        for element in page.elements:
            if options.text_only and element.type in VISUAL_TYPES:
                excluded.append(element.id)
                continue
            common: dict[str, Any] = {
                "id": element.id,
                "role": element.type.value,
                "content": element.content,
                "source": _fragments(element, options.coordinates),
                "method": element.provenance.method.value,
                "engine": element.provenance.engine,
            }
            if isinstance(element, TableElement):
                node = TableNode(
                    **common,
                    rows=element.rows,
                    columns=element.columns,
                    cells=[
                        Cell(
                            id=cell.id,
                            row=cell.row,
                            column=cell.column,
                            row_span=cell.row_span,
                            col_span=cell.col_span,
                            is_header=cell.is_header,
                            text=cell.text,
                            kind=cell.kind,
                            source=Fragment(
                                page=cell.page or element.page,
                                bbox=_bounds(cell.bbox) if options.coordinates else None,
                            ),
                        )
                        for cell in element.cells
                    ]
                    if options.tables.grid
                    else None,
                    markdown=element.markdown if options.tables.markdown else None,
                    html=element.html if options.tables.html else None,
                )
            elif element.type in VISUAL_TYPES:
                asset_id = getattr(element, "asset_id", None)
                asset = (document.assets or {}).get(asset_id or "")
                state: Literal["omitted", "available", "unavailable"]
                state = (
                    "omitted"
                    if options.images == ImageInclusionMode.NONE
                    else ("available" if asset and (asset.path or asset.data_uri) else "unavailable")
                )
                if asset_id and asset:
                    used_assets.add(asset_id)
                else:
                    asset_id = None
                node = VisualNode(
                    **common,
                    asset_id=asset_id,
                    availability=state,
                    parent_table_id=element.metadata.get("parent_table_id"),
                )
            elif element.type == ElementType.LIST:
                items = parse_list(element.content)
                node = ListNode(
                    **common,
                    ordered=ordered(items),
                    items=items,
                )
            else:
                node = TextNode(
                    **common,
                    level=getattr(element, "level", None),
                    original_text=element.metadata.get("original_text"),
                    source_texts=element.metadata.get("source_texts"),
                    normalization_map=element.metadata.get("normalization_map"),
                    recovery_reason=element.metadata.get("recovery_reason"),
                )
            if element.id in nodes:
                raise ValueError(f"Duplicate extracted node ID: {element.id}")
            nodes[element.id] = node
            order.append(element.id)
    assets = {}
    for key in used_assets:
        original = (document.assets or {})[key]
        omitted = options.images == ImageInclusionMode.NONE
        path = original.path if options.images == ImageInclusionMode.PATH else None
        data_uri = original.data_uri if options.images == ImageInclusionMode.BASE64 else None
        assets[key] = Asset(
            id=key,
            mime_type=original.mime_type,
            sha256=original.sha256 or None,
            width=original.width,
            height=original.height,
            availability="omitted" if omitted else "available" if path or data_uri else "unavailable",
            path=None if omitted else path,
            data_uri=None if omitted else data_uri,
        )
    sections = (
        [
            Section(
                id=s.id,
                title=s.title,
                level=s.level,
                parent_id=s.parent_id,
                node_ids=[key for key in s.element_ids if key in nodes],
            )
            for s in document.sections or []
        ]
        if options.hierarchy
        else None
    )
    allowed = set(nodes) | {s.id for s in sections or []}
    relationships = (
        [
            Relation(id=r.id, type=r.type.value, source_id=r.source_id, target_id=r.target_id)
            for r in document.relationships or []
            if r.source_id in allowed and r.target_id in allowed
        ]
        if options.relationships
        else None
    )
    placements: dict[int, list[str]] = {p.page_number: [] for p in document.pages}
    for key in order:
        for source_page in dict.fromkeys(f.page for f in nodes[key].source):
            if source_page not in placements:
                raise ValueError(f"Node {key} references an unprocessed page")
            placements[source_page].append(key)
    omitted_fields = []
    if not options.coordinates:
        omitted_fields.append("coordinates")
    for key in ("metadata", "hierarchy", "relationships"):
        if not getattr(options, key):
            omitted_fields.append(key)
    if not options.tables.grid:
        omitted_fields.append("table_cells")
    metadata = document.metadata.model_dump(mode="json") if document.metadata and options.metadata else None
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "engine": document.engine_version,
                "source": document.metadata.sha256 if document.metadata else document.id,
                "config": document.extraction_config,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return CanonicalDocument(
        document_id=document.id,
        extraction_id=f"extraction_{fingerprint}",
        engine_version=document.engine_version,
        source=Source(
            filename=document.source_file,
            sha256=document.metadata.sha256 if document.metadata else "",
            page_count=document.scope.source_pages,
        ),
        scope=document.scope,
        status=extraction_status(document.issues, document.scope.failed_pages),
        metadata=metadata,
        pages=[
            Page(
                page_number=p.page_number,
                width=p.width,
                height=p.height,
                rotation=p.rotation,
                node_ids=placements[p.page_number],
            )
            for p in document.pages
        ],
        nodes=nodes,
        reading_order=order,
        assets=assets,
        sections=sections,
        relationships=relationships,
        issues=document.issues,
        projection=Projection(excluded_node_ids=excluded, omitted_fields=omitted_fields),
        diagnostics={
            "document": document.quality.model_dump(mode="json") if document.quality else None,
            "pages": {str(p.page_number): p.quality.model_dump(mode="json") for p in document.pages},
        }
        if options.quality_scores
        else None,
    )

"""Section-aware, budgeted chunks with deterministic IDs and precise provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from trueparse.chunking.tokenization import token_counter
from trueparse.contracts.chunks import Chunk, ChunkSourceSpan
from trueparse.exporters.tables import TableRenderer
from trueparse.models.document import Document, Section
from trueparse.models.elements import GenericElement, TableElement
from trueparse.models.enums import ChunkStrategy

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])(?:\s+|$)")


@dataclass
class _Piece:
    element: GenericElement
    text: str
    start: int = 0
    end: int = 0
    row_start: int | None = None
    row_end: int | None = None


def _trim(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _prefix_end(text: str, start: int, budget: int, count: Callable[[str], int]) -> int:
    low = start + 1
    high = min(len(text), start + max(8, budget * 4))
    while high < len(text) and count(text[start:high]) <= budget:
        high = min(len(text), start + (high - start) * 2)
    best = start
    while low <= high:
        middle = (low + high) // 2
        if count(text[start:middle]) <= budget:
            best, low = middle, middle + 1
        else:
            high = middle - 1
    # One Unicode character may itself exceed a tiny token budget. Never lose it.
    return max(start + 1, best)


def _ranges(text: str, budget: int, overlap: int, count: Callable[[str], int]) -> list[tuple[int, int]]:
    ranges = []
    cursor = 0
    while cursor < len(text):
        start, _ = _trim(text, cursor, len(text))
        if start == len(text):
            break
        end = _prefix_end(text, start, budget, count)
        if end < len(text):
            minimum = start + max(1, (end - start) // 2)
            sentences = [
                match.end() for match in _SENTENCE_RE.finditer(text, start, end) if match.end() >= minimum
            ]
            if sentences:
                end = sentences[-1]
            else:
                space = text.rfind(" ", minimum, end)
                if space > minimum:
                    end = space
        _, end = _trim(text, start, end)
        end = max(start + 1, end)
        ranges.append((start, end))
        if end == len(text):
            break
        next_start = end
        if overlap:
            low, high = start + 1, end
            while low < high:
                middle = (low + high) // 2
                if count(text[middle:end]) <= overlap:
                    high = middle
                else:
                    low = middle + 1
            next_start = low
        cursor = max(start + 1, next_start)
    return ranges


def _render(pieces: list[_Piece]) -> str:
    value = ""
    previous = None
    for piece in pieces:
        separator = (
            ""
            if previous is not None
            and previous.element.id == piece.element.id
            and previous.end == piece.start
            else "\n\n"
        )
        value += (separator if value else "") + piece.text
        previous = piece
    return value


class DocumentChunker:
    @classmethod
    def chunk(
        cls,
        document: Document,
        strategy: ChunkStrategy = ChunkStrategy.HYBRID,
        max_tokens: int = 512,
        overlap_tokens: int = 64,
        include_tables: bool = True,
        tokenizer: str = "approximate",
        count_tokens: Callable[[str], int] | None = None,
    ) -> list[Chunk]:
        if max_tokens < 1 or overlap_tokens < 0:
            raise ValueError("Chunk budget must be positive and overlap nonnegative.")
        if overlap_tokens >= max_tokens:
            overlap_tokens = max_tokens // 4
        count = count_tokens or token_counter(tokenizer)
        name = "custom" if count_tokens is not None else tokenizer
        index = {section.id: section for section in document.sections or []}
        mapping = cls._map_elements_to_sections(document.sections or [])
        groups = cls._group_by_section(document, mapping)
        if strategy == ChunkStrategy.TOKEN:
            groups = [("sec_root", [element for _, elements in groups for element in elements])]
        result: list[Chunk] = []
        for section_id, elements in groups:
            section = index.get(section_id)
            path = cls._section_path(section, index)
            buffer: list[_Piece] = []
            budget = None if strategy == ChunkStrategy.SECTION else max_tokens

            def emit(pieces, limit=budget, section=section, path=path):
                if pieces:
                    result.append(cls._build(document, section, path, pieces, count, name, limit))

            for element in elements:
                if isinstance(element, TableElement):
                    if include_tables:
                        emit(buffer)
                        buffer = []
                        for piece in cls._table_pieces(element, max_tokens, count):
                            emit([piece], max_tokens)
                    continue
                start, end = _trim(element.content, 0, len(element.content))
                if end <= start:
                    continue
                piece = _Piece(element, element.content[start:end], start, end)
                if budget is not None and count(piece.text) > budget:
                    emit(buffer)
                    buffer = []
                    for first, last in _ranges(element.content, budget, overlap_tokens, count):
                        emit([_Piece(element, element.content[first:last], first, last)])
                    continue
                if budget is not None and buffer and count(_render(buffer + [piece])) > budget:
                    emit(buffer)
                    buffer = cls._tail(buffer, overlap_tokens, count)
                    while buffer and count(_render(buffer + [piece])) > budget:
                        first_piece = buffer[0]
                        if len(first_piece.text) <= 1:
                            buffer.pop(0)
                        else:
                            buffer[0] = _Piece(
                                first_piece.element,
                                first_piece.text[1:],
                                first_piece.start + 1,
                                first_piece.end,
                            )
                buffer.append(piece)
            emit(buffer)
        for index_number, chunk in enumerate(result):
            chunk.chunk_index = index_number
            digest = hashlib.sha256(
                (
                    chunk.text
                    + json.dumps([span.model_dump() for span in chunk.source_spans], sort_keys=True)
                ).encode()
            ).hexdigest()[:12]
            chunk.id = f"{document.id}:chunk:{index_number:05d}:{digest}"
        return result

    @staticmethod
    def _tail(pieces, budget, count):
        if not budget:
            return []
        tail: list[_Piece] = []
        for piece in reversed(pieces):
            if count(_render([piece] + tail)) <= budget:
                tail.insert(0, piece)
                continue
            low, high = 0, len(piece.text)
            while low < high:
                middle = (low + high) // 2
                candidate = _Piece(piece.element, piece.text[middle:], piece.start + middle, piece.end)
                if count(_render([candidate] + tail)) <= budget:
                    high = middle
                else:
                    low = middle + 1
            if low < len(piece.text):
                tail.insert(0, _Piece(piece.element, piece.text[low:], piece.start + low, piece.end))
            break
        return tail

    @staticmethod
    def _map_elements_to_sections(sections: list[Section]) -> dict[str, str]:
        return {element_id: section.id for section in sections for element_id in section.element_ids}

    @staticmethod
    def _section_path(section: Section | None, index: dict[str, Section]) -> list[str]:
        path, seen = [], set()
        while section is not None and section.id not in seen:
            seen.add(section.id)
            if section.level > 0:
                path.append(section.title)
            section = index.get(section.parent_id) if section.parent_id else None
        return list(reversed(path))

    @staticmethod
    def _group_by_section(document, mapping):
        groups: list[tuple[str, list[GenericElement]]] = []
        for page in document.pages:
            for element in page.elements:
                if not element.content.strip() and not isinstance(element, TableElement):
                    continue
                section = mapping.get(element.id, "sec_root")
                if not groups or groups[-1][0] != section:
                    groups.append((section, []))
                groups[-1][1].append(element)
        return groups

    @staticmethod
    def _table_pieces(table: TableElement, budget, count) -> list[_Piece]:
        TableRenderer.ensure_representations(table, markdown=True, html=False)
        text = table.markdown or table.content
        lines = text.strip().splitlines()
        if len(lines) < 3:
            return [_Piece(table, text, row_start=0, row_end=table.rows)]
        header_rows = max(
            1, max((cell.row + cell.row_span for cell in table.cells if cell.is_header), default=1)
        )
        header_rows = min(header_rows, len(lines) - 1)
        if header_rows == len(lines) - 1:
            return [_Piece(table, text, row_start=0, row_end=table.rows)]
        header = "\n".join(lines[: header_rows + 1])
        pieces = []
        rows: list[str] = []
        first_row = header_rows
        for row_index, row in enumerate(lines[header_rows + 1 :], header_rows):
            candidate = header + "\n" + "\n".join(rows + [row])
            if rows and count(candidate) > budget:
                pieces.append(
                    _Piece(table, header + "\n" + "\n".join(rows), row_start=first_row, row_end=row_index)
                )
                rows = []
                first_row = row_index
            rows.append(row)
        if rows:
            pieces.append(
                _Piece(table, header + "\n" + "\n".join(rows), row_start=first_row, row_end=len(lines) - 1)
            )
        return pieces

    @staticmethod
    def _source_spans(piece: _Piece) -> list[ChunkSourceSpan]:
        element = piece.element
        if isinstance(element, TableElement):
            selected: dict[int, list] = {}
            for cell in element.cells:
                if (
                    cell.is_header
                    or cell.row == 0
                    or (piece.row_start is not None and piece.row_start <= cell.row < (piece.row_end or 0))
                ):
                    selected.setdefault(cell.page or element.page, []).append(cell)
            if not selected:
                return [
                    ChunkSourceSpan(
                        element_id=element.id,
                        page=element.page,
                        bbox=element.bbox.to_list() if element.bbox else None,
                        representation="table",
                        row_start=piece.row_start,
                        row_end=piece.row_end,
                    )
                ]
            spans = []
            for page, cells in selected.items():
                rows = sorted({cell.row for cell in cells})
                runs: list[list[int]] = []
                for row in rows:
                    if not runs or row != runs[-1][-1] + 1:
                        runs.append([])
                    runs[-1].append(row)
                for run in runs:
                    boxes = [cell.bbox for cell in cells if cell.row in run and cell.bbox]
                    bbox = (
                        [
                            min(box.x0 for box in boxes),
                            min(box.y0 for box in boxes),
                            max(box.x1 for box in boxes),
                            max(box.y1 for box in boxes),
                        ]
                        if boxes
                        else (element.bbox.to_list() if element.bbox and page == element.page else None)
                    )
                    spans.append(
                        ChunkSourceSpan(
                            element_id=element.id,
                            page=page,
                            bbox=bbox,
                            representation="table",
                            row_start=run[0],
                            row_end=run[-1] + 1,
                        )
                    )
            return spans
        segments = element.metadata.get("source_segments") or [
            {
                "element_id": element.id,
                "page": element.page,
                "bbox": element.bbox.to_list() if element.bbox else None,
                "start": 0,
                "end": len(element.content),
            }
        ]
        spans = []
        for segment in segments:
            first, last = max(piece.start, segment["start"]), min(piece.end, segment["end"])
            if first < last:
                spans.append(
                    ChunkSourceSpan(
                        element_id=element.id,
                        source_element_id=segment["element_id"],
                        page=segment["page"],
                        bbox=segment.get("bbox"),
                        start=first,
                        end=last,
                    )
                )
        return spans

    @classmethod
    def _build(cls, document, section, path, pieces, count, tokenizer, budget):
        text = _render(pieces)
        spans = [span for piece in pieces for span in cls._source_spans(piece)]
        pages = sorted({span.page for span in spans})
        boxes, seen = [], set()
        for span in spans:
            if span.bbox is None:
                continue
            key = (span.page, tuple(span.bbox), span.element_id)
            if key not in seen:
                boxes.append({"page": span.page, "bbox": span.bbox})
                seen.add(key)
        return Chunk(
            id="pending",
            document_id=document.id,
            text=text,
            token_estimate=count(text),
            tokenizer=tokenizer,
            budget_exceeded=budget is not None and count(text) > budget,
            source_spans=spans,
            section_id=section.id if section else None,
            section_path=path,
            page_start=pages[0] if pages else 0,
            page_end=pages[-1] if pages else 0,
            bboxes=boxes,
            element_ids=list(dict.fromkeys(piece.element.id for piece in pieces)),
            element_types=sorted({piece.element.type.value for piece in pieces}),
            asset_ids=list(
                dict.fromkeys(
                    asset_id for piece in pieces if (asset_id := getattr(piece.element, "asset_id", None))
                )
            ),
        )

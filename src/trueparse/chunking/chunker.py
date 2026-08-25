"""Turns a parsed document into retrieval-ready chunks.

The value TrueParse adds over a plain text splitter is provenance: every chunk
knows the heading path it sits under, which pages and bounding boxes it came
from, and which element and asset IDs produced it. That lets a RAG application
cite an answer back to a rectangle on a page.

Two rules shape the output:
  * Tables are never split. A table is emitted as its own chunk carrying the
    reconstructed Markdown, because half a table retrieves as noise.
  * Splits prefer section boundaries over token boundaries, falling back to a
    token budget only inside sections that are too large.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from trueparse.core.enums import ChunkStrategy, ElementType
from trueparse.core.models import (
    Chunk,
    Document,
    GenericElement,
    Section,
    TableElement,
)

#: Element types that carry no retrievable prose.
_SKIPPED_TYPES = {
    ElementType.HEADER,
    ElementType.FOOTER,
    ElementType.PAGE_NUMBER,
}

_WORD_RE = re.compile(r"\S+")

#: Sentence boundary: terminal punctuation followed by whitespace.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

#: Rough tokens-per-word ratio; avoids a heavy tokenizer dependency.
_TOKENS_PER_WORD = 1.3


def estimate_tokens(text: str) -> int:
    """Approximates the token count of ``text`` without a tokenizer dependency."""
    return int(len(_WORD_RE.findall(text)) * _TOKENS_PER_WORD) + 1


class DocumentChunker:
    """Splits a :class:`Document` into :class:`Chunk` objects."""

    @classmethod
    def chunk(
        cls,
        document: Document,
        strategy: ChunkStrategy = ChunkStrategy.HYBRID,
        max_tokens: int = 512,
        overlap_tokens: int = 64,
        include_tables: bool = True,
    ) -> list[Chunk]:
        """Chunks ``document`` for embedding.

        Args:
            document: A parsed document.
            strategy: See :class:`~trueparse.core.enums.ChunkStrategy`.
            max_tokens: Soft ceiling per chunk; tables may exceed it.
            overlap_tokens: Tokens of tail context repeated into the next chunk.
            include_tables: Emit table chunks alongside prose.

        Returns:
            Chunks in document reading order, each with full provenance.
        """
        if overlap_tokens >= max_tokens:
            overlap_tokens = max(0, max_tokens // 4)

        section_index = {s.id: s for s in document.sections}
        element_to_section = cls._map_elements_to_sections(document.sections)

        groups = cls._group_by_section(document, element_to_section)

        chunks: list[Chunk] = []
        for section_id, elements in groups:
            section = section_index.get(section_id)
            path = cls._section_path(section, section_index)

            if strategy == ChunkStrategy.SECTION:
                chunks.extend(
                    cls._emit(document, section, path, elements, include_tables, budget=None)
                )
            else:
                chunks.extend(
                    cls._emit(
                        document, section, path, elements, include_tables,
                        budget=max_tokens, overlap=overlap_tokens,
                    )
                )

        for idx, chunk in enumerate(chunks):
            chunk.chunk_index = idx
            chunk.id = f"chunk_{idx:05d}"
        return chunks

    @staticmethod
    def _map_elements_to_sections(sections: list[Section]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for section in sections:
            for element_id in section.element_ids:
                mapping[element_id] = section.id
        return mapping

    @staticmethod
    def _section_path(
        section: Section | None,
        index: dict[str, Section],
    ) -> list[str]:
        """Walks parent links to build the heading breadcrumb for a section."""
        if section is None:
            return []
        path: list[str] = []
        seen: set[str] = set()
        cursor: Section | None = section
        while cursor is not None and cursor.id not in seen:
            seen.add(cursor.id)
            # The synthetic root carries no meaningful heading text.
            if cursor.level > 0:
                path.append(cursor.title)
            cursor = index.get(cursor.parent_id) if cursor.parent_id else None
        return list(reversed(path))

    @classmethod
    def _group_by_section(
        cls,
        document: Document,
        element_to_section: dict[str, str],
    ) -> list[tuple[str, list[GenericElement]]]:
        """Collects elements in reading order, grouped by owning section."""
        groups: list[tuple[str, list[GenericElement]]] = []
        current_id: str | None = None
        current: list[GenericElement] = []

        for page in document.pages:
            for element in page.elements:
                if element.type in _SKIPPED_TYPES:
                    continue
                if not (element.content or "").strip():
                    continue

                section_id = element_to_section.get(element.id, "sec_root")
                if section_id != current_id:
                    if current:
                        groups.append((current_id or "sec_root", current))
                    current_id = section_id
                    current = []
                current.append(element)

        if current:
            groups.append((current_id or "sec_root", current))
        return groups

    @classmethod
    def _emit(
        cls,
        document: Document,
        section: Section | None,
        path: list[str],
        elements: list[GenericElement],
        include_tables: bool,
        budget: int | None,
        overlap: int = 0,
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        buffer: list[GenericElement] = []
        buffer_tokens = 0

        def flush() -> None:
            nonlocal buffer, buffer_tokens
            if buffer:
                chunks.append(cls._build(document, section, path, buffer))
                if overlap > 0 and budget:
                    tail = cls._overlap_tail(buffer, overlap)
                    buffer = list(tail)
                    buffer_tokens = sum(estimate_tokens(e.content) for e in buffer)
                else:
                    buffer = []
                    buffer_tokens = 0

        for element in elements:
            # Tables stand alone so retrieval never returns a fragment of a grid.
            if isinstance(element, TableElement):
                if not include_tables:
                    continue
                flush()
                buffer, buffer_tokens = [], 0
                chunks.append(cls._build(document, section, path, [element]))
                continue

            tokens = estimate_tokens(element.content)

            # A single element larger than the whole budget must be split, or
            # one long paragraph would produce an unembeddable chunk.
            if budget and tokens > budget:
                flush()
                buffer, buffer_tokens = [], 0
                for piece in cls._split_text(element.content, budget, overlap):
                    chunks.append(
                        cls._build(document, section, path, [element], text_override=piece)
                    )
                continue

            if budget and buffer and buffer_tokens + tokens > budget:
                flush()
            buffer.append(element)
            buffer_tokens += tokens

        if buffer:
            chunks.append(cls._build(document, section, path, buffer))
        return chunks

    @staticmethod
    def _split_text(text: str, budget: int, overlap: int) -> list[str]:
        """Splits oversize prose on sentence boundaries, falling back to words.

        Sentences are preferred so a chunk boundary rarely lands mid-thought;
        a single sentence longer than the budget is split on whitespace.
        """
        sentences = _SENTENCE_RE.split(text.strip())
        units: list[str] = []
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if estimate_tokens(sentence) <= budget:
                units.append(sentence)
                continue
            # Hard-wrap a runaway sentence at the word level.
            words = sentence.split()
            step = max(1, int(budget / _TOKENS_PER_WORD))
            units.extend(
                " ".join(words[i:i + step]) for i in range(0, len(words), step)
            )

        pieces: list[str] = []
        current: list[str] = []
        current_tokens = 0

        for unit in units:
            unit_tokens = estimate_tokens(unit)
            if current and current_tokens + unit_tokens > budget:
                pieces.append(" ".join(current))
                # Carry a tail of the previous piece forward as context.
                tail: list[str] = []
                tail_tokens = 0
                for previous in reversed(current):
                    previous_tokens = estimate_tokens(previous)
                    if tail_tokens + previous_tokens > overlap:
                        break
                    tail.insert(0, previous)
                    tail_tokens += previous_tokens
                current = tail
                current_tokens = tail_tokens
            current.append(unit)
            current_tokens += unit_tokens

        if current:
            pieces.append(" ".join(current))
        return pieces or [text]

    @staticmethod
    def _overlap_tail(elements: list[GenericElement], overlap: int) -> list[GenericElement]:
        """Returns the trailing elements worth roughly ``overlap`` tokens."""
        tail: list[GenericElement] = []
        total = 0
        for element in reversed(elements):
            if isinstance(element, TableElement):
                break
            tokens = estimate_tokens(element.content)
            if total + tokens > overlap and tail:
                break
            tail.insert(0, element)
            total += tokens
        return tail

    @classmethod
    def _build(
        cls,
        document: Document,
        section: Section | None,
        path: list[str],
        elements: list[GenericElement],
        text_override: str | None = None,
    ) -> Chunk:
        """Assembles one chunk.

        ``text_override`` carries a single slice of an oversize element, so the
        chunk keeps that element's provenance while holding only part of it.
        """
        parts: list[str] = []
        asset_ids: list[str] = []

        for element in elements:
            if isinstance(element, TableElement) and element.markdown:
                parts.append(element.markdown)
            else:
                parts.append(element.content)
            asset_id = getattr(element, "asset_id", None)
            if asset_id:
                asset_ids.append(asset_id)

        text = (
            text_override.strip()
            if text_override is not None
            else "\n\n".join(p for p in parts if p.strip()).strip()
        )
        pages = sorted({e.page for e in elements})

        return Chunk(
            id="chunk_00000",  # replaced with the final index by chunk()
            document_id=document.id,
            chunk_index=0,
            text=text,
            token_estimate=estimate_tokens(text),
            section_id=section.id if section else None,
            section_path=path,
            page_start=pages[0] if pages else 0,
            page_end=pages[-1] if pages else 0,
            bboxes=[
                {"page": e.page, "bbox": e.bbox.to_list()} for e in elements
            ],
            element_ids=[e.id for e in elements],
            element_types=sorted({e.type.value for e in elements}),
            asset_ids=asset_ids,
        )

    @staticmethod
    def to_jsonl(chunks: Iterable[Chunk]) -> str:
        """Serialises chunks as newline-delimited JSON, one chunk per line."""
        return "\n".join(chunk.model_dump_json() for chunk in chunks)

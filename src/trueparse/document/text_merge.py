"""Rejoins prose that PDF layout split apart.

A sentence broken across a column or page boundary arrives as two independent
blocks, and a word broken by a line-break arrives hyphenated. Both survive into
Markdown and into embeddings unless they are repaired, so this pass runs after
reading order is established and before sections are built.
"""
from __future__ import annotations

import re

from trueparse.core.enums import ElementType
from trueparse.core.models import GenericElement

#: A word split across lines; only lower-case tails are rejoined.
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\n([a-z])")

#: Characters that plausibly end a sentence or a deliberate fragment.
_TERMINATORS = ".!?:;\"'）)]}»”’…"

#: Types whose content is prose and may therefore be merged.
_MERGEABLE = {ElementType.PARAGRAPH, ElementType.LIST}


def dehyphenate(text: str) -> str:
    """Repairs words broken by a hyphen at a line break."""
    return _HYPHEN_BREAK_RE.sub(r"\1\2", text)


def _ends_open(text: str) -> bool:
    """True when ``text`` looks like it was cut off mid-sentence."""
    stripped = text.rstrip()
    if not stripped:
        return False
    return stripped[-1] not in _TERMINATORS


def _starts_continuation(text: str) -> bool:
    """True when ``text`` looks like the tail of an interrupted sentence."""
    stripped = text.lstrip()
    if not stripped:
        return False
    first = stripped[0]
    # A lower-case letter, or joining punctuation, means the sentence was
    # already underway. A capital or a bullet means a fresh start.
    return first.islower() or first in ",;)"


def merge_paragraphs(pages_elements: list[list[GenericElement]]) -> list[list[GenericElement]]:
    """Joins prose blocks split across columns and pages, in place.

    Merging is intentionally conservative: a block is only absorbed into its
    predecessor when the predecessor ends without terminal punctuation *and*
    the block itself begins like a continuation. The absorbed block is dropped
    from the output and its identifier recorded in the survivor's metadata so
    element-level provenance is not silently lost.

    Args:
        pages_elements: Per-page element lists, already in reading order.

    Returns:
        New per-page lists with continuations folded into their predecessors.
    """
    # Flatten to a single reading-order stream so merges can cross page breaks.
    flat: list[tuple[int, GenericElement]] = [
        (page_idx, element)
        for page_idx, elements in enumerate(pages_elements)
        for element in elements
    ]

    survivors: list[tuple[int, GenericElement]] = []

    for page_idx, element in flat:
        element.content = dehyphenate(element.content)

        if (
            survivors
            and element.type in _MERGEABLE
            and survivors[-1][1].type in _MERGEABLE
            and _ends_open(survivors[-1][1].content)
            and _starts_continuation(element.content)
        ):
            previous = survivors[-1][1]
            # A hyphen at the join means the word itself was split.
            if previous.content.rstrip().endswith("-"):
                previous.content = previous.content.rstrip()[:-1] + element.content.lstrip()
            else:
                previous.content = f"{previous.content.rstrip()} {element.content.lstrip()}"

            merged_ids = previous.metadata.setdefault("merged_element_ids", [])
            merged_ids.append(element.id)
            if element.page != previous.page:
                previous.metadata["spans_pages"] = sorted(
                    {previous.page, element.page}
                )
            continue

        survivors.append((page_idx, element))

    rebuilt: list[list[GenericElement]] = [[] for _ in pages_elements]
    for page_idx, element in survivors:
        rebuilt[page_idx].append(element)

    # Reading order numbers are now sparse; renumber within each page.
    for elements in rebuilt:
        for idx, element in enumerate(elements):
            element.reading_order = idx + 1

    return rebuilt

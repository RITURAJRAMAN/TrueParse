from __future__ import annotations

import re

from trueparse.models.elements import GenericElement
from trueparse.models.enums import ElementType

_HYPHEN_BREAK_RE = re.compile(r"(?<=\w)-\n(?=[a-z])")
_TERMINATORS = ".!?:;\"'）)]}»”’…"


def dehyphenate(text: str) -> str:
    return _HYPHEN_BREAK_RE.sub("", text)


def _normalize(text, source_id):
    parts, mappings, cursor, position = [], [], 0, 0
    for match in [*_HYPHEN_BREAK_RE.finditer(text), None]:
        end = match.start() if match else len(text)
        if end > cursor:
            part = text[cursor:end]
            parts.append(part)
            mappings.append(
                {
                    "start": position,
                    "end": position + len(part),
                    "source_id": source_id,
                    "source_start": cursor,
                    "source_end": end,
                }
            )
            position += len(part)
        if match:
            cursor = match.end()
    return "".join(parts), mappings


def _clip(mappings, start, end, offset=0):
    result = []
    for mapping in mappings:
        left, right = max(start, mapping["start"]), min(end, mapping["end"])
        if right <= left:
            continue
        item = {**mapping, "start": left + offset, "end": right + offset}
        if mapping.get("source_id") is not None:
            item["source_start"] = mapping["source_start"] + left - mapping["start"]
            item["source_end"] = item["source_start"] + right - left
        result.append(item)
    return result


def merge_paragraphs(pages_elements: list[list[GenericElement]]) -> list[list[GenericElement]]:
    survivors: list[tuple[int, GenericElement]] = []
    for page_idx, elements in enumerate(pages_elements):
        for element in elements:
            if element.type == ElementType.PARAGRAPH:
                original = element.content
                element.content, mapping = _normalize(original, element.id)
            else:
                original, mapping = (
                    element.content,
                    [
                        {
                            "start": 0,
                            "end": len(element.content),
                            "source_id": element.id,
                            "source_start": 0,
                            "source_end": len(element.content),
                        }
                    ]
                    if element.content
                    else [],
                )
            element.metadata["original_text"] = original
            element.metadata["source_texts"] = {element.id: original}
            element.metadata["normalization_map"] = mapping
            element.metadata["source_segments"] = [
                {
                    "element_id": element.id,
                    "page": element.page,
                    "bbox": element.bbox.to_list() if element.bbox else None,
                    "start": 0,
                    "end": len(element.content),
                }
            ]
            previous = survivors[-1][1] if survivors else None
            before = previous.content.rstrip() if previous is not None else ""
            after = element.content.lstrip()
            if not (
                previous
                and previous.type == element.type == ElementType.PARAGRAPH
                and before
                and after
                and before[-1] not in _TERMINATORS
                and (after[0].islower() or after[0] in ",;)")
            ):
                survivors.append((page_idx, element))
                continue
            hyphenated = before.endswith("-")
            kept = len(before) - int(hyphenated)
            separator = "" if hyphenated else " "
            leading = len(element.content) - len(after)
            offset = kept + len(separator) - leading
            merged_mapping = _clip(previous.metadata["normalization_map"], 0, kept)
            if separator:
                merged_mapping.append({"start": kept, "end": kept + 1})
            merged_mapping.extend(_clip(mapping, leading, len(element.content), offset))
            previous.content = before[:kept] + separator + after
            previous.metadata["normalization_map"] = merged_mapping
            previous.metadata["source_texts"].update(element.metadata["source_texts"])
            previous.metadata["original_text"] += "\n" + original
            for segment in previous.metadata["source_segments"]:
                segment["end"] = min(segment["end"], kept)
            previous.metadata["source_segments"].extend(
                {**segment, "start": max(leading, segment["start"]) + offset, "end": segment["end"] + offset}
                for segment in element.metadata["source_segments"]
            )
            previous.metadata.setdefault("merged_element_ids", []).append(element.id)
            previous.metadata["spans_pages"] = sorted(
                {fragment["page"] for fragment in previous.metadata["source_segments"]}
            )
    rebuilt: list[list[GenericElement]] = [[] for _ in pages_elements]
    for page_idx, element in survivors:
        rebuilt[page_idx].append(element)
    for elements in rebuilt:
        for index, element in enumerate(elements, 1):
            element.reading_order = index
    return rebuilt

from __future__ import annotations
from typing import Sequence
import re

from trueparse.core.enums import RelationshipType, ElementType
from trueparse.core.models import (
    GenericElement,
    Relationship,
    Section,
    TableElement,
    FigureElement,
    ChartElement,
)


class DocumentGraphBuilder:
    """Constructs the relational graph connecting sections, elements, captions, and references."""

    @classmethod
    def build_relationships(
        cls,
        sections: Sequence[Section],
        pages_elements: Sequence[Sequence[GenericElement]],
    ) -> list[Relationship]:
        relationships: list[Relationship] = []
        rel_counter = 1

        # 1. Section contains element relationships
        for sec in sections:
            for elem_id in sec.element_ids:
                relationships.append(
                    Relationship(
                        id=f"rel_{rel_counter:04d}",
                        type=RelationshipType.CONTAINS,
                        source_id=sec.id,
                        target_id=elem_id,
                    )
                )
                rel_counter += 1

            if sec.parent_id:
                relationships.append(
                    Relationship(
                        id=f"rel_{rel_counter:04d}",
                        type=RelationshipType.SECTION_CHILD,
                        source_id=sec.parent_id,
                        target_id=sec.id,
                    )
                )
                rel_counter += 1

        # 2. Captions & cross references
        all_elements_flat: list[GenericElement] = [
            el for page in pages_elements for el in page
        ]
        elements_by_id = {el.id: el for el in all_elements_flat}

        for elem in all_elements_flat:
            # Check caption association
            caption_target_id = None
            if hasattr(elem, "target_element_id") and getattr(elem, "target_element_id"):
                caption_target_id = getattr(elem, "target_element_id")
            elif "target_element_id" in elem.metadata:
                caption_target_id = elem.metadata["target_element_id"]

            if caption_target_id and caption_target_id in elements_by_id:
                relationships.append(
                    Relationship(
                        id=f"rel_{rel_counter:04d}",
                        type=RelationshipType.HAS_CAPTION,
                        source_id=caption_target_id,
                        target_id=elem.id,
                    )
                )
                rel_counter += 1

            # In-text references (e.g. "see Figure 1", "in Table 3")
            if elem.type in (ElementType.PARAGRAPH, ElementType.LIST):
                matches = re.findall(r"\b(Figure|Fig\.|Table|Chart)\s+(\d+)\b", elem.content, re.IGNORECASE)
                for label, num in matches:
                    label_clean = label.lower()
                    target_type = ElementType.TABLE if "table" in label_clean else (
                        ElementType.CHART if "chart" in label_clean else ElementType.FIGURE
                    )
                    # Find candidate element with matching caption
                    for candidate in all_elements_flat:
                        if candidate.type == target_type:
                            if candidate.content and num in candidate.content:
                                relationships.append(
                                    Relationship(
                                        id=f"rel_{rel_counter:04d}",
                                        type=RelationshipType.REFERENCES,
                                        source_id=elem.id,
                                        target_id=candidate.id,
                                        metadata={"ref_label": f"{label} {num}"},
                                    )
                                )
                                rel_counter += 1
                                break

        return relationships

from __future__ import annotations
from typing import Sequence
import re

from trueparse.core.enums import ElementType
from trueparse.core.models import (
    CaptionElement,
    DocumentElement,
    GenericElement,
    HeadingElement,
    Section,
    TableElement,
    FigureElement,
    ChartElement,
)


class HierarchyEngine:
    """Builds document sections, headings hierarchy, and caption associations."""

    @classmethod
    def build_sections_and_captions(
        cls,
        pages_elements: list[list[GenericElement]],
    ) -> tuple[list[Section], list[list[GenericElement]]]:
        sections: list[Section] = []
        current_section: Optional[Section] = None
        section_counter = 1

        # Create a default document root section
        root_section = Section(
            id="sec_root",
            title="Document Root",
            level=0,
            parent_id=None,
            element_ids=[],
        )
        sections.append(root_section)
        current_section = root_section

        updated_pages_elements: list[list[GenericElement]] = []

        for page_elements in pages_elements:
            updated_page: list[GenericElement] = []
            
            # Identify captions and associate with nearby tables/figures/charts
            for idx, elem in enumerate(page_elements):
                # Check for headings
                if isinstance(elem, HeadingElement) or elem.type in (ElementType.TITLE, ElementType.SECTION_HEADER):
                    sec_id = f"sec_{section_counter:04d}"
                    section_counter += 1
                    level = elem.level if isinstance(elem, HeadingElement) else (1 if elem.type == ElementType.TITLE else 2)
                    
                    new_section = Section(
                        id=sec_id,
                        title=elem.content[:100],
                        level=level,
                        parent_id=root_section.id if level == 1 else (current_section.id if current_section else root_section.id),
                        element_ids=[elem.id],
                    )
                    sections.append(new_section)
                    current_section = new_section
                    if isinstance(elem, HeadingElement):
                        elem.section_id = sec_id
                else:
                    if current_section:
                        current_section.element_ids.append(elem.id)
                    else:
                        root_section.element_ids.append(elem.id)

                # Check if caption
                if elem.type == ElementType.CAPTION:
                    # Look ahead or behind for target figure/table/chart on same page
                    caption_target_id = None
                    caption_text_lower = elem.content.lower()

                    for other in page_elements:
                        if other.id == elem.id:
                            continue
                        # If caption says "Table 1" and other is table
                        if "table" in caption_text_lower and other.type == ElementType.TABLE:
                            if abs(other.bbox.y1 - elem.bbox.y0) < 50 or abs(elem.bbox.y1 - other.bbox.y0) < 50:
                                caption_target_id = other.id
                                if isinstance(other, TableElement):
                                    other.caption_id = elem.id
                                break
                        elif ("fig" in caption_text_lower or "chart" in caption_text_lower) and other.type in (ElementType.FIGURE, ElementType.CHART, ElementType.DIAGRAM):
                            if abs(other.bbox.y1 - elem.bbox.y0) < 50 or abs(elem.bbox.y1 - other.bbox.y0) < 50:
                                caption_target_id = other.id
                                if isinstance(other, (FigureElement, ChartElement)):
                                    other.caption_id = elem.id
                                break

                    if isinstance(elem, CaptionElement):
                        elem.target_element_id = caption_target_id
                    elif caption_target_id:
                        elem.metadata["target_element_id"] = caption_target_id

                updated_page.append(elem)
            updated_pages_elements.append(updated_page)

        return sections, updated_pages_elements

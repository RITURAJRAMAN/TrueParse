from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel, Field

from trueparse.models.text import RawTextBlock


class FontProfile(BaseModel):
    """Document-wide typography statistics used to classify headings.

    Computing this across the whole document rather than per page fixes two
    common failure modes: a page consisting entirely of a heading (which has no
    body text to compare against) and a document whose body size drifts between
    sections.
    """

    body_size: float = Field(description="Character-weighted modal body font size")
    #: Distinct heading styles, largest first. Index + 1 is the heading level.
    ladder: list[float] = Field(default_factory=list)
    body_is_bold: bool = Field(
        default=False,
        description="True when body text is predominantly bold, disabling the bold heuristic",
    )

    def level_for(self, size: float, is_bold: bool) -> int | None:
        """Maps a font size onto a heading level, or None if it is body text."""
        for idx, rung in enumerate(self.ladder):
            # Sizes within a quarter point are the same style in practice.
            if size >= rung - 0.25:
                return idx + 1
        if is_bold and not self.body_is_bold and size >= self.body_size - 0.25:
            # Bold at body size is the weakest signal; park it below the ladder.
            return max(1, len(self.ladder)) + 1
        return None


class TypographyAnalyzer:
    @classmethod
    def build_font_profile(
        cls,
        blocks: Iterable[RawTextBlock],
        max_levels: int = 4,
    ) -> FontProfile:
        """Derives the body size and heading ladder from every block in a document.

        Body size is the *character-weighted* mode: a document with 40 short
        headings and 6 dense paragraphs must still resolve the paragraph size
        as body, which an unweighted median gets wrong.
        """
        blocks = list(blocks)
        if not blocks:
            return FontProfile(body_size=10.0, ladder=[])

        weighted: Counter[float] = Counter()
        bold_chars = 0
        total_chars = 0
        for block in blocks:
            size = round(block.avg_font_size, 1)
            weighted[size] += max(1, block.char_count)
            total_chars += max(1, block.char_count)
            if block.is_bold:
                bold_chars += max(1, block.char_count)

        body_size = weighted.most_common(1)[0][0]
        body_is_bold = total_chars > 0 and (bold_chars / total_chars) > 0.6

        # Heading sizes: larger than body, and not a big share of the text.
        candidates = sorted(
            (
                size
                for size, chars in weighted.items()
                if size >= body_size + 0.5 and chars / total_chars < 0.35
            ),
            reverse=True,
        )

        # Collapse sizes within half a point; they are the same visual style.
        ladder: list[float] = []
        for size in candidates:
            if not ladder or (ladder[-1] - size) > 0.5:
                ladder.append(size)
            if len(ladder) >= max_levels:
                break

        return FontProfile(body_size=body_size, ladder=ladder, body_is_bold=body_is_bold)

from __future__ import annotations

from pydantic import BaseModel


class BoundingBox(BaseModel):
    """Unrotated PDF-page coordinates in points (1/72 inch), measured from the top left."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def to_list(self) -> list[float]:
        return [round(self.x0, 2), round(self.y0, 2), round(self.x1, 2), round(self.y1, 2)]

    @classmethod
    def from_rect(cls, rect: tuple[float, float, float, float] | list[float]) -> BoundingBox:
        return cls(x0=rect[0], y0=rect[1], x1=rect[2], y1=rect[3])

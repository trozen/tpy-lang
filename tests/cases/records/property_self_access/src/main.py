# Properties accessed via self inside class methods
from tpy import int32

class Rect:
    _w: int32
    _h: int32

    def __init__(self, w: int32, h: int32) -> None:
        self._w = w
        self._h = h

    @property
    def width(self) -> int32:
        return self._w

    @width.setter
    def width(self, v: int32) -> None:
        self._w = v

    @property
    def height(self) -> int32:
        return self._h

    @property
    def area(self) -> int32:
        return self.width * self.height

    def describe(self) -> str:
        return f"{self.width}x{self.height}={self.area}"

    def scale(self, factor: int32) -> None:
        self.width = self._w * factor

def main() -> None:
    r = Rect(3, 4)
    print(r.describe())
    r.scale(2)
    print(r.describe())

main()

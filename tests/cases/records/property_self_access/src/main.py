# Properties accessed via self inside class methods
from tpy import Int32

class Rect:
    _w: Int32
    _h: Int32

    def __init__(self, w: Int32, h: Int32) -> None:
        self._w = w
        self._h = h

    @property
    def width(self) -> Int32:
        return self._w

    @width.setter
    def width(self, v: Int32) -> None:
        self._w = v

    @property
    def height(self) -> Int32:
        return self._h

    @property
    def area(self) -> Int32:
        return self.width * self.height

    def describe(self) -> str:
        return f"{self.width}x{self.height}={self.area}"

    def scale(self, factor: Int32) -> None:
        self.width = self._w * factor

def main() -> None:
    r = Rect(3, 4)
    print(r.describe())
    r.scale(2)
    print(r.describe())

main()

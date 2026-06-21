# ValueType records: value (copy) semantics, immutable, nested + with methods.
from tpy import Int32, ValueType


class Vec2(ValueType):
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    def total(self) -> Int32:
        return self.x + self.y


# Rect uses Vec2 -- nested value types.
class Rect(ValueType):
    pos: Vec2
    size: Vec2

    def __init__(self, pos: Vec2, size: Vec2) -> None:
        self.pos = pos
        self.size = size

    def origin_sum(self) -> Int32:
        return self.pos.total()


def main() -> None:
    a = Vec2(1, 2)
    b = a                      # copy (value type)
    print(a.total())           # 3
    print(b.total())           # 3

    r = Rect(Vec2(0, 0), Vec2(10, 20))
    print(r.pos.x)             # 0
    print(r.size.total())      # 30
    print(r.origin_sum())      # 0


main()

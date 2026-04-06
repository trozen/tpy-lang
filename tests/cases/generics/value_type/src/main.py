# Test ValueType marker protocol: records with value semantics.
from tpy import Int32, ValueType


class Vec2(ValueType):
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


# Rect uses Vec2 -- tests nested value types
class Rect(ValueType):
    pos: Vec2
    size: Vec2

    def __init__(self, pos: Vec2, size: Vec2) -> None:
        self.pos = pos
        self.size = size


def modify(v: Vec2) -> None:
    v.x = 99


def main() -> None:
    a = Vec2(1, 2)

    # Assignment copies (value type, not reference)
    b = a
    b.x = 10
    print(a.x)                 # 1
    print(b.x)                 # 10

    # Function parameter is a copy (value type passed by value)
    modify(a)
    print(a.x)                 # 1

    # Nested value types are also copied
    r = Rect(Vec2(0, 0), Vec2(10, 20))
    r2 = r
    r2.pos.x = 99
    print(r.pos.x)             # 0
    print(r2.pos.x)            # 99

main()

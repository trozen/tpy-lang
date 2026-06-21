# Error: augmented assignment to a value-type field is also rejected
# (value types are immutable).
from tpy import Int32, ValueType


class Vec2(ValueType):
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def bump(v: Vec2) -> None:
    v.x += 1  # tpyc: error(/Cannot assign to field 'x' of immutable value type 'Vec2'/)


def main() -> None:
    bump(Vec2(1, 2))


main()

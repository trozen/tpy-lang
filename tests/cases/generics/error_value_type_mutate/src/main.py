# Error: a value-type field cannot be assigned outside the class's own
# __init__ -- here, mutating a value-type parameter. The admitted
# assignment inside __init__ is pinned by tests/cases/generics/value_type.
from tpy import Int32, ValueType


class Vec2(ValueType):
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def modify(v: Vec2) -> None:
    v.x = 99  # tpyc: error(/Cannot assign to field 'x' of immutable value type 'Vec2'/)


def main() -> None:
    modify(Vec2(1, 2))


main()

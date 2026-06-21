# Error: a non-__init__ method of a value type cannot mutate self
# (value types are immutable).
from tpy import Int32, ValueType


class Vec2(ValueType):
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    def bump(self) -> None:
        self.x = self.x + 1  # tpyc: error(/Cannot assign to field 'x' of immutable value type 'Vec2'/)


def main() -> None:
    pass


main()

# Error: a non-__init__ method of a value type cannot mutate self
# (value types are immutable).
from tpy import int32, ValueType


class Vec2(ValueType):
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def bump(self) -> None:
        self.x = self.x + 1  # tpyc: error(/Cannot assign to field 'x' of immutable value type 'Vec2'/)


def main() -> None:
    pass


main()

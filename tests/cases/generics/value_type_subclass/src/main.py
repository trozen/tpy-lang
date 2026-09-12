# A value-type subclass with no __init__ of its own satisfies the
# explicit-__init__ requirement via the inherited one.
from tpy import int32, ValueType


class Base(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Derived(Base, ValueType):
    def double(self) -> int32:
        return self.x + self.x


def main() -> None:
    d = Derived(7)
    print(d.x)
    print(d.double())


main()

# A method mixing a protocol param with a value-form record default keeps its
# C++ default: the protocol emit path must see the callable as a member too.

from tpy import Int64, ValueType
from typing import Protocol


class Vec(ValueType):
    def __init__(self, x: Int64) -> None:
        self.x = x


class Drawable(Protocol):
    def draw(self) -> Int64: ...


class Circle:
    def __init__(self, r: Int64) -> None:
        self.r = r

    def draw(self) -> Int64:
        return self.r


class Holder:
    def __init__(self, base: Int64) -> None:
        self.base = base

    # The protocol param routes this signature through the protocol emit path;
    # `Vec | None` is value-form over a record, the shape whose default is
    # suppressed on a FREE function. As a member it must keep its C++ default.
    def method(self, d: Drawable, v: Vec | None = None) -> Int64:
        got = self.base + d.draw()
        if v is None:
            return got
        return got + v.x


def main() -> None:
    h = Holder(100)
    c = Circle(5)
    print(h.method(c))  # v omitted: relies on the declaration's default
    print(h.method(c, Vec(7)))


main()

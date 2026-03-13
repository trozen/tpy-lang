# Error: mutating the result of a @auto_readonly method called on a readonly receiver.
# The const overload returns readonly[T], so downstream mutation must be rejected.
from typing import overload
from tpy import Int32, readonly, auto_readonly

class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def set_x(self, v: Int32) -> None:
        self.x = v

class Container[T]:
    _data: list[T]

    def __init__(self) -> None:
        self._data = []

    def add(self, item: T) -> None:
        self._data.append(item)

    @auto_readonly
    def get(self, index: Int32) -> auto_readonly[T]:
        return self._data[index]

def bad(c: readonly[Container[Point]]) -> None:
    p = c.get(Int32(0))   # tpyc: type(readonly[Point])
    p.set_x(Int32(99))    # tpyc: error(/Cannot call non-readonly method 'set_x' on readonly reference/)

def main() -> None:
    c = Container[Point]()
    c.add(Point(Int32(1)))
    bad(c)

main()

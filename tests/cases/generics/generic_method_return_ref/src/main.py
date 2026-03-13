# Test: method returning T inside a generic function binds a reference (val_or_ref_t)
from __future__ import annotations
from typing import Protocol
from tplib import Box


class Mutable(Protocol):
    def mutate(self) -> None: ...


class Point:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x

    def mutate(self) -> None:
        self.x += 10


def process[T: Mutable](box: Box[T]) -> None:
    item = box.get()  # tpyc: type(T)  -- val_or_ref_t<T>: Point& for records
    item.mutate()     # mutation through the reference


def test() -> None:
    b = Box[Point](Point(1))
    process(b)
    print(b.get().x)  # 11: mutation in process() was visible, so item was a ref not a copy


test()

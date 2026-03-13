# Free generic function returning T binds a reference (val_or_ref_t) in a generic caller.
# Parallel to generic_method_return_ref but for TpyCall instead of TpyMethodCall.
from __future__ import annotations
from typing import Protocol


class Mutable(Protocol):
    def mutate(self) -> None: ...


class Point:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x

    def mutate(self) -> None:
        self.x += 10


def first[T](items: list[T]) -> T:
    return items[0]


def process[T: Mutable](items: list[T]) -> None:
    item = first(items)  # tpyc: type(T)  -- val_or_ref_t<T>: Point& for records
    item.mutate()        # mutation through the reference


def test() -> None:
    pts = [Point(1), Point(2)]
    process(pts)
    print(pts[0].x)  # 11: mutation was visible, so item was a ref not a copy


test()

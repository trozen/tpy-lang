# Generator yielding tuple[T | None, ...] preserves references like CPython:
# the iterator slot is `std::tuple<T*, T*>` (borrow form), so mutations
# through the yielded tuple flow back to the iterable. Parallel to gen_ref
# / gen_ref_while but for the pointer-repr-Optional tuple shape.
from typing import Iterator
from tpy import Int32


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def pairs(items: list[P]) -> Iterator[tuple[P | None, P | None]]:
    for it in items:
        yield (it, None)


def main() -> None:
    points = [P(1), P(2), P(3)]
    for a, b in pairs(points):
        if a is not None:
            a.x = a.x * 10
    for p in points:
        print(p.x)


main()

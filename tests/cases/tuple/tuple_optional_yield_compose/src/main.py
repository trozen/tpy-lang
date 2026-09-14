# Outer single-yield generator iterates an inner generator yielding
# tuple[T | None, ...]. Exercises the direct-iterator branch (the inner
# generator IS the iterator passed to `for`) and confirms borrow form
# survives composition: the loop var `auto&&` binds to the inner's
# __next__() return type without a storage-form type mismatch.
from typing import Iterator
from tpy import int32


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def first_only(items: list[P]) -> Iterator[tuple[P | None, P | None]]:
    for it in items:
        yield (it, None)


def relay(src: Iterator[tuple[P | None, P | None]]) -> Iterator[tuple[P | None, P | None]]:
    for pair in src:
        yield pair


def main() -> None:
    points = [P(1), P(2), P(3)]
    # Mutate through the relayed yielded tuple -- mutation must flow back
    # through both generator boundaries to `points`.
    for a, b in relay(first_only(points)):
        if a is not None:
            a.x = a.x * 10
    for p in points:
        print(p.x)


main()

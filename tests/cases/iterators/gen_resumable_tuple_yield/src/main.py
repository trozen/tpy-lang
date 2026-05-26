# Tuple-yield generator on the resumable path. The iterator slot is
# borrow form (`std::tuple<P*, P*>`); exercises multi-yield through
# while + if/else decomposition with pointer-form Optional tuple
# elements. Distinct from tuple_optional_yield_prev (for-loop +
# sentinel reset + trailing yield).
from typing import Iterator
from tpy import Int32


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def pairs(items: list[P]) -> Iterator[tuple[P | None, P | None]]:
    n = Int32(len(items))
    i: Int32 = 0
    while i < n:
        if i + 1 < n:
            yield (items[i], items[i + 1])
        else:
            yield (items[i], None)
        i += 2


def show(pair: tuple[P | None, P | None]) -> None:
    a, b = pair
    if a is not None:
        print(a.x)
    else:
        print("a=none")
    if b is not None:
        print(b.x)
    else:
        print("b=none")
    print("---")


def main() -> None:
    items = [P(1), P(2), P(3)]
    for pair in pairs(items):
        show(pair)


main()

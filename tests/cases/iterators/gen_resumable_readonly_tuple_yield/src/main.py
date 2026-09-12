# Multi-yield generator with a readonly[tuple[P, P]] yield type on the
# resumable path. The __next__ slot must be the const-borrow form
# (std::expected<std::tuple<const P&, const P&>, StopIteration>) and the
# yielded tuple literal must construct const-ref element slots -- a regression
# guard for _iter_slot_for_yield / _gen_tuple_literal peeling ReadonlyType.
from typing import Iterator
from tpy import int32, readonly


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def pairs(items: list[P]) -> Iterator[readonly[tuple[P, P]]]:
    n = int32(len(items))
    i: int32 = 0
    while i + 1 < n:
        if i == 0:
            yield (items[i], items[i + 1])
        else:
            yield (items[i + 1], items[i])
        i += 2


def main() -> None:
    items = [P(1), P(2), P(3), P(4)]
    for pair in pairs(items):
        print(pair[0].x)
        print(pair[1].x)


main()

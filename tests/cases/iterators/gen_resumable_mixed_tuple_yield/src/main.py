# Mixed value+reference tuple yield on the resumable path: the __next__ slot is
# std::expected<std::tuple<int32_t, P&>, StopIteration>. A tuple with an lvalue
# reference member is NOT move-assignable, so this is a regression guard that
# the emit path only ever CONSTRUCTS / RETURNS the expected (never assigns it):
# the body `return std::tuple<...>{...}` and the consumer's per-iteration
# `auto __r = it.__next__()` both construct, so the ref-tuple is legal.
from typing import Iterator
from tpy import int32


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def pairs(items: list[P]) -> Iterator[tuple[int32, P]]:
    n = int32(len(items))
    i: int32 = 0
    while i < n:
        if i == 0:
            yield (i, items[i])
        else:
            yield (i * 10, items[i])
        i += 1


def main() -> None:
    items = [P(1), P(2), P(3)]
    for idx, p in pairs(items):
        print(idx)
        print(p.x)


main()

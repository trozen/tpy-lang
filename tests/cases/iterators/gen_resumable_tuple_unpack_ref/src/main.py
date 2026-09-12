# Tuple-unpack for-loop in a generator where a REFERENCE element is
# mutated and read across a yield. The reference target aliases the live
# container element (pointer-form `T*`), so the mutation propagates to the
# source list -- matching CPython and the plain pointer-form loop var. The
# value element (idx) copies. Both survive the yield.
from typing import Iterator
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def process(rows: list[tuple[int32, Item]]) -> Iterator[int32]:  # tpyc: ok
    for idx, it in rows:
        it.n = idx * 100      # mutate the reference element
        yield idx             # suspend; idx (value) and it (ref) must survive
        it.n = it.n + idx     # read + mutate the same element after resume


def main() -> None:
    rows: list[tuple[int32, Item]] = [(1, Item(0)), (2, Item(0))]
    for v in process(rows):
        print(v)
    # Mutations through the unpacked reference propagated to the source.
    print(rows[0][1].n, rows[1][1].n)


main()

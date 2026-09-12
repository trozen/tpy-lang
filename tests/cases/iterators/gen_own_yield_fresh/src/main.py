# Iterator[Own[T]] yields freshly-constructed owned values (moved out of the
# frame, not borrowed). Each iteration produces a distinct object, so a consumer
# may keep them -- the owned ABI is the declaration-driven counterpart to the
# borrow ABI. The multi-yield sections take the RESUMABLE FRAME and cover the
# other owning sources: `copy(<loop var>)` and a call declaring `-> Own[T]`.
# Copy semantics are the point of those sections, so each mutates its source
# after the yield and shows the yielded value unchanged.
from typing import Iterator
from tpy import Own, Int32, copy


class Node:
    val: int

    def __init__(self, v: int):
        self.val = v


def boxes(n: int) -> Iterator[Own[Node]]:
    for i in range(n):
        yield Node(i)


def mk(v: Int32) -> Own[Node]:
    return Node(v)


def mk_row(v: Int32) -> Own[list[Int32]]:
    return [v]


# free generator, two yields (frame): `copy(<frame loop var>)` at an owning
# record slot, and a call declaring `-> Own[Node]`.
def fresh_records(src: list[Node]) -> Iterator[Own[Node]]:
    for p in src:
        yield copy(p)  # tpyc: ok
        yield mk(p.val * 10)  # tpyc: ok


# `Own[CONTAINER]` slot -- the same admission over the reference axis's
# container half, not just records.
def fresh_rows(src: list[list[Int32]]) -> Iterator[Own[list[Int32]]]:
    for r in src:
        yield copy(r)  # tpyc: ok
        yield mk_row(len(r))  # tpyc: ok


class Bag:
    items: list[Node]

    def __init__(self) -> None:
        self.items = [Node(7)]

    # generator METHOD, same two owning sources.
    def drain(self) -> Iterator[Own[Node]]:
        for p in self.items:
            yield copy(p)  # tpyc: ok
            yield mk(p.val + 1)  # tpyc: ok


def main() -> None:
    total = 0
    for b in boxes(4):
        total = total + b.val
    print("boxes", total)

    # free: the yielded value is a COPY -- mutating the source afterwards
    # leaves it alone.
    src = [Node(3)]
    kept = []
    for b2 in fresh_records(src):
        kept.append(b2.val)
    src[0].val = 100
    print("free", kept, src[0].val)

    rows: list[list[Int32]] = [[1, 2]]
    kept_rows = []
    for r in fresh_rows(rows):
        kept_rows.append(len(r))
    rows[0].append(9)
    print("container", kept_rows, len(rows[0]))

    bag = Bag()
    kept_m = []
    for b3 in bag.drain():
        kept_m.append(b3.val)
    bag.items[0].val = 55
    print("method", kept_m, bag.items[0].val)


main()

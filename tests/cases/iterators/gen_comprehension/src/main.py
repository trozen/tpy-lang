# A resumable generator frame is iterable in list/set/dict comprehensions, not
# just for-loops.
# The Own[Node] case collects into list[Node] by moving each owned element
# (the consuming-for-append move, applied to comprehension element sinks).
from typing import Iterator, Iterable
from tpy import int32, Own


class Node:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v


class Counter:
    base: int32
    def __init__(self, base: int32) -> None:
        self.base = base
    def around(self) -> Iterator[int32]:
        yield self.base - 1
        yield self.base
        yield self.base + 1


def two_then(n: int32) -> Iterator[int32]:
    yield 0
    i: int32 = 1
    while i <= n:
        yield i
        i += 1


# Iterating an Iterable[T] param makes the frame struct templated.
def head[T](it: Iterable[T], n: int32) -> Iterator[T]:
    c: int32 = 0
    for x in it:
        if c >= n:
            break
        yield x
        c += 1


# Single-yield tail-loop generator: the same frame, at the plainest shape.
def simple(n: int32) -> Iterator[int32]:
    i: int32 = 0
    while i < n:
        yield i
        i += 1


def pairs(n: int32) -> Iterator[tuple[int32, int32]]:
    yield (0, 0)
    i: int32 = 1
    while i < n:
        yield (i, i * i)
        i += 1


def make_nodes(n: int32) -> Iterator[Own[Node]]:
    yield Node(0)
    i: int32 = 1
    while i < n:
        yield Node(i)
        i += 1


def main() -> None:
    print([x for x in two_then(3)])               # [0, 1, 2, 3]
    print([x for x in two_then(4) if x % 2 == 0])  # [0, 2, 4]
    nums: list[int32] = [10, 20, 30, 40]
    print([x for x in head(nums, 2)])             # [10, 20]
    s = {x for x in two_then(2)}
    print(len(s))                                 # 3
    d = {x: x * x for x in two_then(2)}
    print(d[2])                                   # 4
    print([x for x in simple(3)])                 # [0, 1, 2]

    # generator method
    print([x for x in Counter(10).around()])      # [9, 10, 11]
    # tuple yield, unpacked in the comprehension
    print([a + b for a, b in pairs(3)])           # [0, 2, 6]
    # generator that yields nothing past the first element
    print([x for x in head(nums, 0)])             # []
    # reference-type (Own) yield collected into a list
    xs = [node for node in make_nodes(3)]
    print(len(xs), xs[2].v)                       # 3 2


main()

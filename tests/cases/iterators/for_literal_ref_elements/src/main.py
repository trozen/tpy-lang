# A list LITERAL as a for-source over REFERENCE elements: the loop var aliases
# the element, so the capture has to own a real container -- a bare brace-init
# deduces std::initializer_list, whose elements are const. Every section
# mutates the loop var through that alias; the @nocopy section makes a silent
# per-element copy a compile error rather than a parity-blind pass.
from typing import Iterator

import asyncio

from tpy import int32, nocopy


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


@nocopy
class Tag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


# free function
def in_function() -> None:
    for b in [Box(0), Box(1)]:  # tpyc: ok
        b.bump()
        print("function", b.n)


# free function, @nocopy element
def nocopy_element() -> None:
    for t in [Tag(10), Tag(20)]:  # tpyc: ok
        t.bump()
        print("nocopy", t.n)


# free function, container element
def container_element() -> None:
    for xs in [[1], [2, 3]]:  # tpyc: ok
        xs.append(9)
        print("container", len(xs))


class Holder:
    total: int32

    # constructor
    def __init__(self) -> None:
        self.total = 0
        for b in [Box(2), Box(3)]:  # tpyc: ok
            b.bump()
            self.total += b.n

    # method
    def run(self) -> None:
        for b in [Box(4), Box(5)]:  # tpyc: ok
            b.bump()
            print("method", b.n)


# generator
def in_generator() -> Iterator[int32]:
    total = 0
    for b in [Box(6), Box(7)]:  # tpyc: ok
        b.bump()
        total += b.n
    yield total


# async
async def in_async() -> None:
    for b in [Box(8), Box(9)]:  # tpyc: ok
        b.bump()
        print("async", b.n)


# closure
def in_closure() -> None:
    def inner() -> None:
        for b in [Box(11), Box(12)]:  # tpyc: ok
            b.bump()
            print("closure", b.n)

    inner()


def main() -> None:
    in_function()
    nocopy_element()
    container_element()
    h = Holder()
    print("constructor", h.total)
    h.run()
    for v in in_generator():
        print("generator", v)
    asyncio.run(in_async())
    in_closure()


main()

# module level
for mb in [Box(13), Box(14)]:  # tpyc: ok
    mb.bump()
    print("module", mb.n)

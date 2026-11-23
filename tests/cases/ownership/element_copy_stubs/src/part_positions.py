# The receiver copy at every call position: method, generator,
# comprehension, closure. Each copies silently
# (BUGS.md#list-copy-concat-elements-copy-unwarned).
from typing import Iterator
from tpy import int32, Own


class B:
    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    items: list[B]

    def __init__(self) -> None:
        self.items = [B(1), B(2)]

    def snapshot(self) -> Own[list[B]]:
        # method: a field receiver
        return self.items.copy()  # tpyc: ok


def count(xs: list[B]) -> int32:
    return len(xs)


def gen(xs: list[B]) -> Iterator[Own[list[B]]]:
    # generator
    yield xs.copy()  # tpyc: ok


def main() -> None:
    h = Holder()
    print("method:", len(h.snapshot()))
    xs = [B(3), B(4)]
    for ys in gen(xs):
        print("generator:", len(ys))
    xss = [[B(5)], [B(6), B(7)]]
    # comprehension
    lens = [count(x + x) for x in xss]  # tpyc: ok
    print("comprehension:", lens)

    def inner() -> int32:
        # closure
        return count(xs[::2])  # tpyc: ok
    print("closure:", inner())

# A fresh argument at a method called through a protocol-typed receiver: not
# a twin route (the receiver's type is a template), so it keeps its rows.
# Each receiver iterates what its method reads: the protocol's concept probes
# the method with the receiver itself (BUGS.md#protocol-param-structural-protocol-concept).
from typing import Iterable, Iterator, Protocol, Sized
from tpy import int32
from fixtures import Acc


class Reader(Protocol):
    def read(self, xs: Iterable[Acc]) -> int32: ...


class IntReader(Protocol):
    def read_ints(self, xs: Iterable[int32]) -> int32: ...


class R:
    held: list[Acc]

    def __init__(self) -> None:
        self.held = [Acc(0)]

    def __iter__(self) -> Iterator[Acc]:
        return iter(self.held)

    def read(self, xs: Iterable[Acc]) -> int32:
        s = 0
        for a in xs:
            a.bump()
            s += a.get()
        return s


class RI:
    held: list[int32]

    def __init__(self) -> None:
        self.held = [0]

    def __iter__(self) -> Iterator[int32]:
        return iter(self.held)

    def read_ints(self, xs: Iterable[int32]) -> int32:
        s = 0
        for x in xs:
            s += x
        return s


def measure(x: Sized) -> int32:
    return len(x)


def via(r: Reader, ri: IntReader, k: int32) -> None:
    # a list literal of records at the receiver's method, written through it
    print("recv literal", r.read([Acc(3), Acc(k)]))  # tpyc: ok
    # a comprehension at the receiver's method
    print("recv comp", ri.read_ints([i for i in range(k)]))  # tpyc: ok


def cond_repeat(n: int32) -> None:
    # a list repeat at a Sized parameter in a conditional operand
    print("cond repeat", measure([1] * n) if n > 0 else 0)  # tpyc: ok


def main() -> None:
    via(R(), RI(), 4)
    cond_repeat(4)
    cond_repeat(0)

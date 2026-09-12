# Address-of a reference-bound generic param into a Ptr[W] slot, via both a
# field assign and a local binding, with W deduced from the call lvalue.
from typing import Protocol
from tpy import Ptr, Own, int32


class Adds(Protocol):
    def add(self, n: int32) -> None: ...


class Sink(Adds):
    total: int32

    def __init__(self) -> None:
        self.total = 0

    def add(self, n: int32) -> None:
        self.total += n


class Adder[W: Adds]:
    _sink: Ptr[W]

    def __init__(self, sink: W) -> None:
        self._sink = sink

    def push(self, n: int32) -> None:
        self._sink.add(n)


def make_adder[W: Adds](sink: W) -> Own[Adder[W]]:
    p: Ptr[W] = sink
    p.add(0)
    return Adder(sink)


def main() -> None:
    s = Sink()
    a = make_adder(s)
    a.push(10)
    a.push(5)
    # 15 confirms the borrow wrote through to the caller's Sink (no copy).
    print(s.total)


main()

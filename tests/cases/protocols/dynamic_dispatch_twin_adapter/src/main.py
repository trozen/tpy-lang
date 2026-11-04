# A structural conformer with a mutable and a @readonly @dispatch variant of
# one name, reached through a @dynamic protocol: the @readonly slot runs the
# @readonly variant (the ref adapter forwards through a const view), the
# mutable slot the mutable one, through the ref and the owning adapter.
from typing import Protocol
from tpy import dynamic, readonly, dispatch, int32


@dynamic
class Gauge(Protocol):
    @readonly
    def peek(self) -> int32: ...

    def tick(self) -> int32: ...


class Needle:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    @readonly(False)
    def peek(self) -> int32:
        self.n += 1
        return self.n

    @dispatch
    @readonly
    def peek(self) -> int32:
        return self.n + 100

    @dispatch
    @readonly(False)
    def tick(self) -> int32:
        self.n += 10
        return self.n

    @dispatch
    @readonly
    def tick(self) -> int32:
        return self.n + 1000


def read(g: Gauge) -> int32:
    # The @readonly slot: the @readonly variant, which leaves n alone.
    return g.peek()  # tpyc: ok


def step(g: Gauge) -> int32:
    # The mutable slot: the mutable variant, which writes n.
    return g.tick()  # tpyc: ok


def main() -> None:
    n = Needle()
    # Through the ref adapter: the writes reach n.
    print("ref:", read(n), step(n), read(n), n.n)
    g: Gauge = Needle()
    # Through the owning adapter.
    print("owned:", read(g), step(g), read(g))


main()

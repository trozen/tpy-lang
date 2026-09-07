# A walrus over a NESTED field's Optional inside a generator frame: the
# Optional-ptr walrus source admits a direct field read only.
from typing import Iterator
from tpy import Int32, Own


class Node:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Inner:
    item: Node | None

    def __init__(self, n: Own[Node]) -> None:
        self.item = n


class Holder:
    inner: Inner

    def __init__(self, n: Own[Node]) -> None:
        self.inner = Inner(n)

    def g(self) -> Iterator[Int32]:  # tpyc: error(/res\.cond:expr\.walrus/)
        yield -1
        i = 0
        while i < 2:
            # The walrus source is one hop past a direct field read.
            if (p := self.inner.item) is not None:
                yield p.v
            else:
                yield -2
            i += 1


def main() -> None:
    h = Holder(Node(5))
    for u in h.g():
        print(u)


main()

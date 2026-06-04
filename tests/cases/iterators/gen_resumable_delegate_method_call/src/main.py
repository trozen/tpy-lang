# A resumable generator delegating to a generator METHOD call on a local:
# __for_src embeds the method's struct (__gen_Holder_...), and the yielded
# borrows alias the holder's fields -- mutations must persist (CPython
# aliasing).
from typing import Iterator
from tpy import Int32


class Node:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val


class Holder:
    a: Node
    b: Node

    def __init__(self) -> None:
        self.a = Node(1)
        self.b = Node(2)

    def nodes_gen(self) -> Iterator[Node]:
        yield self.a
        yield self.b


def bump_all(h: Holder) -> Iterator[Int32]:
    yield 0
    total = 0
    for n in h.nodes_gen():  # tpyc: ok
        n.val += 10
        total += n.val
        yield total


def main() -> None:
    h = Holder()
    for v in bump_all(h):
        print(v)
    print(h.a.val, h.b.val)


main()

# A container reached through a MULTI-level record field chain is a valid
# subscript receiver: the read/write render as the flat postfix chain.
from tpy import Int32, Own


class Inner:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]


class Outer:
    inner: Inner

    def __init__(self, inner: Own[Inner]) -> None:
        self.inner = inner

    def at(self, i: Int32) -> Int32:
        # The subject inside a method (a `this->` rooted chain).
        return self.inner.items[i]  # tpyc: ok

    def bump(self, i: Int32) -> None:
        self.inner.items[i] += 10  # tpyc: ok


def put(nodes: list[Outer]) -> None:
    # The chain rooted at a container ELEMENT: the element read is a receiver
    # too, so the write lands in the element's own storage, not a copy.
    nodes[0].inner.items[0] = 7  # tpyc: ok


def main() -> None:
    o = Outer(Inner())
    print(o.at(1))
    # ... and off a plain local receiver.
    v = o.inner.items[2]  # tpyc: ok
    print(v)
    o.bump(0)
    # The chain names the record's own storage, so the write is visible.
    print(o.inner.items[0], o.at(0))
    o.inner.items[1] = 42  # tpyc: ok
    print(o.at(1))
    ns = [Outer(Inner())]
    put(ns)
    # The write through the element chain is visible on the list's element.
    print(ns[0].inner.items[0])


main()

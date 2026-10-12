# A @property read through a Ptr, a Deref wrapper (Box, Rc) or an unproven
# Optional reaches the getter through the same hops as the method call does.
import asyncio
from typing import Iterator
from tpy import int32, Ptr
from tplib.box import Box
from tplib.rc import Rc


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    @property
    def twice(self) -> int32:
        return self.v * 2


class Holder:
    p: Ptr[Node]

    def __init__(self, p: Ptr[Node]) -> None:
        self.p = p

    # method: the getter through a Ptr FIELD, then through the narrowed field
    def read(self) -> int32:
        a = self.p.twice  # tpyc: ok nullable(self.p)
        if self.p is not None:
            return a + self.p.twice  # tpyc: ok non_null(self.p)
        return a


# free function: through a Ptr parameter and a Ptr field off a parameter
def via_param(p: Ptr[Node], h: Holder) -> int32:
    return p.twice + h.p.twice  # tpyc: ok nullable(p)


# free function: an unproven Optional receiver takes the runtime null check
def via_optional(n: Node | None) -> int32:
    return n.twice  # tpyc: warning(/Potential None access/) nullable(n)


# generator body: through a Ptr field
def gen(h: Holder) -> Iterator[int32]:
    yield h.p.twice  # tpyc: ok


# async body: through a Ptr field
async def through_async(h: Holder) -> int32:
    return h.p.twice  # tpyc: ok


def main() -> None:
    n = Node(5)
    h = Holder(n)
    print("method", h.read())
    # The Ptr aliases n: a write through n is seen through the getter.
    n.v = 6
    print("method-after", h.read())
    print("param", via_param(n, h))
    print("optional", via_optional(n))
    for x in gen(h):
        print("generator", x)
    print("async", asyncio.run(through_async(h)))
    # Deref wrappers: the getter runs on the wrapped value.
    b = Box(Node(3))
    print("box", b.twice)  # tpyc: ok
    r = Rc.new(Node(4))
    print("rc", r.twice)  # tpyc: ok
    # closure: through a captured Ptr
    p: Ptr[Node] = n

    def inner() -> int32:
        return p.twice  # tpyc: ok
    print("closure", inner())


main()

# The flush-less nested constructor position admits TEMP-FREE sources only
# (calls/ctor_nested_tempfree_args). A container LITERAL at a MUTATED slot is
# not one: the inline brace is a prvalue, a mutable `T&` cannot bind it, and
# there is no statement here to hoist the temp into -- so it keeps rejecting
# where the same literal at an unmutated slot now compiles.
from typing import Protocol
from tpy import dynamic, int32
from tplib import Box, Rc


@dynamic
class DynP(Protocol):
    def ping(self) -> int32: ...


class KMut:
    tag: int32

    def __init__(self, p: list[int32]) -> None:
        p.append(99)
        self.tag = len(p)

    def ping(self) -> int32:
        return self.tag


def main() -> None:
    rc: Rc[Box[DynP]] = Rc.new(Box(KMut([1, 2])))  # tpyc: error(/not yet supported.*ctor_arg.container/)
    print(rc.get().get().ping())


main()

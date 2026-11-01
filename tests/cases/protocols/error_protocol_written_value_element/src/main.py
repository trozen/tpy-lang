# A list element of a value record at a protocol parameter the callee writes is
# refused, as at its concrete twin (BUGS.md#subscript-elem-record), never a C++ error.
from typing import Protocol
from tpy import ValueType, int32


class HasN(Protocol):
    def get(self) -> int32: ...


class V(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def get(self) -> int32:
        return self.n


# `HasN.get` is not @readonly, so the callee writes `p`.
def rebind(p: HasN) -> int32:
    return p.get()


def main() -> None:
    vs = [V(1), V(2)]
    print(rebind(vs[0]))  # tpyc: error(/not yet supported/)


main()

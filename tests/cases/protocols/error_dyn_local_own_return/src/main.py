# A @dynamic local returned at Own[Shape]: its move has no render, so the
# return stays refused (BUGS.md#dyn-local-own-arg-ill-formed).
from typing import Protocol
from tpy import dynamic, Own, int32


@dynamic
class Shape(Protocol):
    def area(self) -> int32: ...


class Sq:
    def __init__(self, n: int32) -> None:
        self.n = n

    def area(self) -> int32:
        return self.n


def ret(k: int32) -> Own[Shape]:
    s: Shape = Sq(k)
    return s  # tpyc: error(/not yet supported/)


def main() -> None:
    print(ret(8).area())


main()

# A @dynamic local at an Own[P] parameter: the erased handle has no copy to
# hand over, so the argument is refused (BUGS.md#dyn-local-own-arg-ill-formed).
from typing import Protocol
from tpy import Own, dynamic, int32


@dynamic
class Shape(Protocol):
    def area(self) -> int32: ...


class Sq:
    def __init__(self, k: int32) -> None:
        self.k = k

    def area(self) -> int32:
        return self.k * self.k


def keep(s: Own[Shape]) -> int32:
    return s.area()


def run(k: int32) -> int32:
    s: Shape = Sq(k)
    return keep(s)  # tpyc: error(/arg\.lift\.borrow/)


def main() -> None:
    print(run(3))


main()

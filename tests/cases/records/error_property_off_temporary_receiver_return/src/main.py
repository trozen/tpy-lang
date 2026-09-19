# The RETURN position of a borrow-returning @property read off a TEMPORARY
# receiver: the caller holds the result after the callee's temporary is gone.
# The tag names the SINK; the sink table is in docs/PROPERTY_DESIGN.md.
from typing import Iterator
from tpy import Own, int32

G: list[int32] = [1, 2]


class H:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    @property
    def items(self) -> list[int32]:
        return G


def mk() -> Own[H]:
    return H()


def borrow() -> list[int32]:
    return mk().items  # tpyc: error(/return.lends_from_temporary/)


def main() -> None:
    print(len(borrow()))


main()

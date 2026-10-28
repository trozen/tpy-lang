# The SETITEM-VALUE position of a borrow-returning @property read off a
# TEMPORARY receiver: `d[1] = mk().items` would copy a borrow of storage the
# statement kills into a container that outlives it, losing every later
# mutation CPython shows through the alias. The read is a borrow, as its
# bound twin `h = mk(); d[1] = h.items` is, and the twin's value-shape gate
# refuses both first (BUGS.md#getter-result-slot-shape-rejects); the sink
# table is in docs/PROPERTY_DESIGN.md.
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


def main() -> None:
    d: dict[int32, list[int32]] = {}
    d[1] = mk().items  # tpyc: error(/setitem\.container_value_shape/)
    print(len(d))


main()

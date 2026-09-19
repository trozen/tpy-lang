# The SETITEM-VALUE position of a borrow-returning @property read off a
# TEMPORARY receiver: `d[1] = mk().items` would copy a borrow of storage the
# statement kills into a container that outlives it, losing every later
# mutation CPython shows through the alias. The tag names the SINK, from the
# one `SinkForm.DYING_SOURCE_LEND` row; the sink table is in
# docs/PROPERTY_DESIGN.md.
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
    d[1] = mk().items  # tpyc: error(/setitem_value.lends_from_temporary/)
    print(len(d))


main()

# A simple generator yielding its LOOP VAR whole, where the loop var is the
# storage-form tuple element of a `list[tuple[Int32, C]]` and the yield slot is
# the pointer-repr borrow form: the yield lifts the name via tuple_to_pointer,
# so the consumer aliases the source element rather than copying it.
from typing import Iterator
from tpy import Int32


class C:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def storage_relay(items: list[tuple[Int32, C]]) -> Iterator[tuple[Int32, C]]:
    for pair in items:
        yield pair  # tpyc: ok


def main() -> None:
    xs: list[tuple[Int32, C]] = [(1, C(5)), (2, C(6))]
    for n, c in storage_relay(xs):
        # Mutating through the yielded element reaches the source list.
        c.v += n * 10
    print([c.v for n, c in xs])


main()

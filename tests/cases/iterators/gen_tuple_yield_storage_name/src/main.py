# A generator yielding its LOOP VAR whole, where the loop var is the storage-form
# tuple element of a `list[tuple[int32, C]]` and the yield slot is the pointer-repr
# borrow form: the yield lifts the name via tuple_to_pointer, so the consumer
# aliases the source element rather than copying it. One section per position; a
# two-yield generator is not "simple", so those sections take the resumable frame
# and the loop var reads through the frame's pointer field.
from typing import Iterator
from tpy import int32, Own


class C:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    items: list[tuple[int32, C]]

    def __init__(self, items: Own[list[tuple[int32, C]]]) -> None:
        self.items = items

    # Method position on the frame: the loop var walks `(*__self.items)`.
    def relay(self) -> Iterator[tuple[int32, C]]:
        for pair in self.items:
            yield pair  # tpyc: ok
            yield pair  # tpyc: ok


def storage_relay(items: list[tuple[int32, C]]) -> Iterator[tuple[int32, C]]:
    for pair in items:
        yield pair  # tpyc: ok


# Free function, two yields.
def relay_twice(items: list[tuple[int32, C]]) -> Iterator[tuple[int32, C]]:
    for pair in items:
        yield pair  # tpyc: ok
        yield pair  # tpyc: ok


def main() -> None:
    xs: list[tuple[int32, C]] = [(1, C(5)), (2, C(6))]
    for n, c in storage_relay(xs):
        # Mutating through the yielded element reaches the source list.
        c.v += n * 10
    print("free", [c.v for n, c in xs])

    for n, c in relay_twice(xs):
        c.v += n
    print("twice", [c.v for n, c in xs])

    h = Holder([(3, C(7)), (4, C(8))])
    for n, c in h.relay():
        c.v += n
    print("method", [c.v for n, c in h.items])


main()

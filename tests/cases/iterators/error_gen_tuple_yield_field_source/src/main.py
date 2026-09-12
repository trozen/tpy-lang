# A storage-form tuple NAME lowers at a pointer-repr yield slot, but a FIELD-read
# source must keep rejecting: the field read has no lowered storage-to-borrow
# lift at the yield slot.
from typing import Iterator
from tpy import int32


class C:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    p: tuple[int32, C]

    def __init__(self, p: tuple[int32, C]) -> None:
        self.p = p

    def relay(self) -> Iterator[tuple[int32, C]]:  # tpyc: error(/sgen.tuple_yield_source/)
        for i in range(1):
            yield self.p


def main() -> None:
    h = H((1, C(2)))
    for n, c in h.relay():
        print(n, c.v)


main()

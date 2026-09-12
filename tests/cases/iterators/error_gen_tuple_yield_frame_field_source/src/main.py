# The resumable twin of error_gen_tuple_yield_field_source: two yields make the
# generator non-simple, so it lowers on the frame. A storage-form tuple NAME
# lowers at a pointer-repr yield slot there too, but a FIELD-read source must keep
# rejecting -- the field read has no lowered storage-to-borrow lift at the slot.
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

    def relay(self) -> Iterator[tuple[int32, C]]:  # tpyc: error(/res.btuple_yield_source/)
        for i in range(1):
            yield self.p
            yield self.p


def main() -> None:
    h = H((1, C(2)))
    for n, c in h.relay():
        print(n, c.v)


main()

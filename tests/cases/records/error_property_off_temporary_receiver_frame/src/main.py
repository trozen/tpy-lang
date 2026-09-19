# The FRAME-SLOT-WRITE position of a borrow-returning @property read off a
# TEMPORARY receiver: a generator's local lives in the frame, which outlives
# every statement in the body, so the slot would hold a copy of a borrow whose
# storage the header statement kills -- silently, where CPython keeps the
# alias. The sink table is in docs/PROPERTY_DESIGN.md.
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


def gen() -> Iterator[int32]:
    v = mk().items  # tpyc: error(/frame_slot_write.lends_from_temporary/)
    yield len(v)


def main() -> None:
    for n in gen():
        print(n)


main()

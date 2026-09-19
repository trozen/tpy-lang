# The GLOBAL-SLOT-WRITE position of a borrow-returning @property read off a
# TEMPORARY receiver. The module-init slot would take a silent copy of the
# borrow, so a later mutation through what the getter lent is invisible where
# CPython shows it. The sink table is in docs/PROPERTY_DESIGN.md.
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


SLOT: list[int32] = mk().items  # tpyc: error(/global_slot_write.lends_from_temporary/)

print(len(SLOT))

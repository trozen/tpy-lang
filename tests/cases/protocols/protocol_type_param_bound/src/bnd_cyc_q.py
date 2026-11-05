# Import-cycle member: a bounded protocol inheriting its peer's bounded protocol.
from typing import Protocol
from tpy import ValueType, int32
import bnd_cyc_p

class Counted[U: ValueType](bnd_cyc_p.Sized[U], Protocol):  # tpyc: ok
    def count(self) -> int32: ...


def tag() -> int32:
    return 5

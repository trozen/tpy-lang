# Import-cycle member defining a bounded protocol; its peer bnd_cyc_q extends it.
from typing import Protocol
from tpy import ValueType, int32
import bnd_cyc_q


class Sized[T: ValueType](Protocol):
    def size(self) -> int32: ...


def peer_tag() -> int32:
    return bnd_cyc_q.tag()

# Optional[T] crosses at the top of a param/return only: as a container
# ELEMENT it has no per-element gate, so `list[Optional[int32]]` stays
# rejected with the located boundary error.
# tpy: ext_module
from typing import Optional
from tpy import int32
from tpy.extern import export


@export
def total(xs: list[Optional[int32]]) -> int32:  # tpyc: error(/parameter 'xs' of type 'list\[int32 \| None\]' cannot cross/)
    t = 0
    for x in xs:
        if x is not None:
            t += x
    return t

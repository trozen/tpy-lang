# The null path of an element read off an unproven `Optional[list]` GLOBAL in
# a module-level statement: the global slot is the nullable pointer, so
# `::tpy::deref_check(G)` panics before the element is read.
from typing import Optional

from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


G: Optional[list[P]] = None
print("before")
print(G[0].x)  # tpyc: warning(/Potential None access/)

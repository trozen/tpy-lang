# `Optional[Own[Cls]]` at a param: the host keeps its reference through the
# None gate too, so ownership can't transfer -- the same rejection as Own[Cls].
# tpy: ext_module
from typing import Optional
from tpy import int64, Own
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a


@export
def take(c: Optional[Own[C]]) -> int64:  # tpyc: error(/Own\[C\].*not a valid boundary type/)
    if c is None:
        return -1
    return c.a

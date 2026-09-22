# `-> Optional[Cls]` over a @nocopy exposed class is the same borrow return
# behind a None gate: the present arm copies the instance out, and the copy
# is deleted -- rejected with the located @nocopy message, as `-> Cls` is.
# tpy: ext_module
from typing import Optional
from tpy import int64, nocopy
from tpy.extern import export


@export
@nocopy
class C:
    def __init__(self, a: int64):
        self.a = a


@export
def maybe(c: C, flag: bool) -> Optional[C]:  # tpyc: error(/@nocopy class 'C' returned by reference/)
    if flag:
        return c
    return None

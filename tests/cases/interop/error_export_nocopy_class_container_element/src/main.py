# A container copies each element in/out at the boundary, so a @nocopy exposed
# class (whose copy ctor is deleted) cannot be a container element -- rejected
# with a located error instead of an opaque "deleted copy constructor" build
# failure. (A @nocopy class still crosses as a plain borrow-form param.)
# tpy: ext_module
from tpy import Int64, nocopy
from tpy.extern import export


@nocopy
@export
class Handle:
    def __init__(self, v: Int64):
        self.value = v


@export
def f(hs: list[Handle]) -> Int64:  # tpyc: error(/@nocopy exposed-class container element 'Handle'/)
    return len(hs)

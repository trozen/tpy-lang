# A @nocopy exposed class returned by reference can't be copied out (copy ctor
# deleted); return Own[Cls] to move it.
# tpy: ext_module
from tpy import Int64, nocopy
from tpy.extern import export


@export
@nocopy
class C:
    def __init__(self, a: Int64):
        self.a = a

    def me(self) -> "C":  # tpyc: error(/method 'me' return is a @nocopy class 'C' returned by reference/)
        return self

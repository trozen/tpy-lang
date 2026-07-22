# A readonly[Cls] borrow return of a @nocopy exposed class hits the same
# located reject as the plain `-> Cls` form: the check peels to the boundary
# inner, so every borrow spelling (RefType or ReadonlyType-topped) lands on
# the one reject.
# tpy: ext_module
from tpy import Int64, nocopy, readonly
from tpy.extern import export


@export
@nocopy
class Res:
    n: Int64

    def __init__(self, n: Int64):
        self.n = n

    def peek(self) -> "readonly[Res]":  # tpyc: error(/method 'peek' return is a @nocopy class 'Res' returned by reference/)
        return self

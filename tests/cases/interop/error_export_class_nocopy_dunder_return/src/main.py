# A @nocopy exposed class returned by borrow from a dunder slot is a located
# error (the slot's fallback copies the instance out; the copy is deleted) --
# the same reject the plain-method validator applies. Identity-only
# enablement (a provable `return self`) is a deferred decision tracked in
# TODO.md.
# tpy: ext_module
from tpy import int64, nocopy
from tpy.extern import export


@export
@nocopy
class Cur:
    n: int64

    def __init__(self, n: int64):
        self.n = n

    def __iter__(self) -> "Cur":  # tpyc: error(/'__iter__' return is a @nocopy class 'Cur' returned by reference/)
        return self

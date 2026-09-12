# A dunder other than __init__ (and the currently-supported repr/str/eq/ne/
# lt/le/gt/ge/hash group) on an exposed class is silently absent from the
# host type; warn so it isn't a surprise. __call__ isn't wired yet.
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a

    def __call__(self) -> int64:  # tpyc: warning(/'__call__' is not exposed to CPython/)
        return self.a

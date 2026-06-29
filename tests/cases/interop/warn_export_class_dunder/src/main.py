# A dunder other than __init__ on an exposed class is silently absent from the
# host type (only __init__ + plain methods cross); warn so it isn't a surprise.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class C:  # tpyc: warning(/'__repr__' is not exposed to CPython/)
    def __init__(self, a: Int64):
        self.a = a

    def __repr__(self) -> str:
        return "C"

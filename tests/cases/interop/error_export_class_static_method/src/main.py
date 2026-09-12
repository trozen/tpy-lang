# A plain (non-dunder) @staticmethod on an exposed class is rejected: the glue
# emits an instance-method wrapper that unwraps a live self payload.
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a

    @staticmethod
    def make() -> int64:  # tpyc: error(/'make' cannot be a @staticmethod/)
        return 0

# An async dunder can't cross the CPython boundary: the compiled method
# returns a coroutine frame, not the value the type slot wrapper marshals.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    async def __repr__(self) -> str:  # tpyc: error(/'__repr__' cannot be async/)
        return "C"

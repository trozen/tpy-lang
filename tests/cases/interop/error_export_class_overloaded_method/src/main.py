# An @overload method on an exposed class can't cross: the glue emits one
# positional wrapper, which would silently expose only the first arity.
# tpy: ext_module
from typing import overload

from tpy import int64
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a

    @overload
    def f(self, x: int64) -> int64: ...  # tpyc: error(/overloaded 'f' cannot be exposed/)
    @overload
    def f(self, x: int64, y: int64) -> int64: ...
    def f(self, x: int64, y: int64 = 0) -> int64:
        return x + y

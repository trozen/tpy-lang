# An @overload method on an exposed class can't cross: the glue emits one
# positional wrapper, which would silently expose only the first arity.
# tpy: ext_module
from typing import overload

from tpy import Int64
from tpy.extern import export


@export
class C:  # tpyc: error(/overloaded 'f' cannot be exposed/)
    def __init__(self, a: Int64):
        self.a = a

    @overload
    def f(self, x: Int64) -> Int64: ...
    @overload
    def f(self, x: Int64, y: Int64) -> Int64: ...
    def f(self, x: Int64, y: Int64 = 0) -> Int64:
        return x + y

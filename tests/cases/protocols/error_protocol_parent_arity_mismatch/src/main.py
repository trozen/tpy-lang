# Inheriting from a generic protocol with a wrong number of type arguments
# (parent has 2, child supplies 1) is rejected by the type-ref resolver
# before sema sees the parent. Pinned here so a future resolver change
# can't silently let mismatched arity through.
from typing import Protocol
from tpy import Int32


class Pair[A, B](Protocol):
    def first(self) -> A: ...
    def second(self) -> B: ...


class Child[T](Pair[T], Protocol):  # tpyc: error(/Pair requires exactly 2 type parameters/)
    pass

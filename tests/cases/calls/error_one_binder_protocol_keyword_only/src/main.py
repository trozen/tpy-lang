# A keyword-only parameter of a protocol method stays keyword-only in a
# protocol inheriting it at a type argument: passing it positionally errors.
from typing import Protocol
from tpy import int32


class P[T](Protocol):
    def f(self, *, x: T) -> T: ...


class Q(P[int32], Protocol):
    pass


class C:
    def f(self, *, x: int32) -> int32:
        return x


def run(q: Q) -> int32:
    return q.f(7)  # tpyc: error(/takes 0 positional argument/)


print(run(C()))

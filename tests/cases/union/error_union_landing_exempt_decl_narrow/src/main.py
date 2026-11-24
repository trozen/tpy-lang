# A declaration at a union two of whose members share one C++ alternative
# narrows to its member, but reading it is refused: the binding's union is
# decided per alternative (BUGS.md#exempt-union-landing-rejects).
from typing import Literal
from tpy import int32


def f() -> None:
    v: Literal["a"] | Literal["b"] | int32 = int32(1)
    print(v + int32(2))  # tpyc: error(/name.union_binding_divergent/)


f()

# A nested def capturing a value record that holds a Ptr captures state: the
# copy carries the pointer, so a borrow result may point into storage the
# nested def's signature says nothing about. A pointer-free value record is no
# state (calls/callable_def_rule, sec_nested_locals).
from typing import Callable
from tpy import int32, Ptr, ValueType


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Handle(ValueType):
    p: Ptr[P]

    def __init__(self, p: Ptr[P]) -> None:
        self.p = p


def outer() -> Callable[[P], P]:
    src = P(11)
    h = Handle(src)

    def get(q: P) -> P:
        return q if h.p.n > 0 else q
    # The subject: get captures h, whose Ptr field points into src.
    f: Callable[[P], P] = get  # tpyc: error(/'get' cannot be passed as Callable\[\[P\], P\]: it captures 'h'/)
    return f


print(outer()(P(2)).n)

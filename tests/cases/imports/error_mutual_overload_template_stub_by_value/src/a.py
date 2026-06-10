# Regression guard for the group-aware header-routing predicate: an @overload
# group whose Iterable[Own[Int32]] stub is a C++ template (protocol param) while
# the impl (a union param) is not is still header-emitted, so a by-value cycle
# peer in the RETURN type must trip the completeness gate. Without the stub-index
# in _is_template_emitted_in_header the impl is not gated and the error is missed
# (the header-emitted template returns B, whose layout is only forward-declared).
from b import B
from tpy import Int32, Own
from typing import Iterable, overload


def helper() -> Int32:
    return 42


@overload
def measure(xs: Iterable[Own[Int32]]) -> B: ...
@overload
def measure(xs: Int32) -> B: ...
def measure(xs: Iterable[Own[Int32]] | Int32) -> B:  # tpyc: error(/Cyclic import/)
    if isinstance(xs, Int32):
        return B(xs)
    n = 0
    for x in xs:
        n += x
    return B(n)

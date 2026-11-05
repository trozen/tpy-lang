# An @overload stub whose Iterable[Own[int32]] parameter makes its variant a
# C++ template is header-emitted, so a by-value cycle peer in its RETURN type
# must trip the completeness gate (the header-emitted template returns B,
# whose layout is only forward-declared there).
from b import B
from tpy import int32, Own
from typing import Iterable, overload


def helper() -> int32:
    return 42


@overload
def measure(xs: Iterable[Own[int32]]) -> B: ...  # tpyc: error(/Cyclic import/)
@overload
def measure(xs: int32) -> B: ...
def measure(xs: Iterable[Own[int32]] | int32) -> B:
    if isinstance(xs, int32):
        return B(xs)
    n = 0
    for x in xs:
        n += x
    return B(n)

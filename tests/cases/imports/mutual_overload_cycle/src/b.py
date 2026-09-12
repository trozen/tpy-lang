# Defines an @overload-grouped function `g` and imports back from `a`
# to close the cycle.
from a import use_g_int
from tpy import int32
from typing import overload

@overload
def g(x: int32) -> int32: ...
@overload
def g(x: str) -> int32: ...
def g(x: int32 | str) -> int32:
    if isinstance(x, int32):
        return x * 2
    return int32(len(x))

def relay(n: int32) -> int32:
    return use_g_int(n)

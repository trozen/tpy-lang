# Defines an @overload-grouped function `g` and imports back from `a`
# to close the cycle.
from a import use_g_int
from tpy import Int32
from typing import overload

@overload
def g(x: Int32) -> Int32: ...
@overload
def g(x: str) -> Int32: ...
def g(x: Int32 | str) -> Int32:
    if isinstance(x, Int32):
        return x * 2
    return Int32(len(x))

def relay(n: Int32) -> Int32:
    return use_g_int(n)

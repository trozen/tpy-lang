# An operator on an `@native` record answers like its explicit dunder call:
# `n * x` with `x: int32` converts at the `int64` variant, as `n.__mul__(x)`
# does, and a native-function comparison converts inside a chain too.
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy import int32, int64, dispatch, readonly
from tpy.extern import native, cpp_template


@native("::ScaleNative")
class N:
    def __init__(self) -> None: ...

    @dispatch
    @readonly
    @cpp_template("{self}.mul_i({0})")
    def __mul__(self, x: int64) -> int32: ...

    @dispatch
    @readonly
    @cpp_template("{self}.mul_f({0})")
    def __mul__(self, x: float) -> int32: ...

    @readonly
    @native("scale_less", function=True)
    def __lt__(self, x: int) -> bool: ...


calls: int32 = 0


def tick() -> int32:
    global calls
    calls += 1
    return 3


def main() -> None:
    n = N()
    x: int32 = 3
    # The operand is received at the `int64` variant, as the explicit call's is.
    print(n * x, n.__mul__(x))  # tpyc: ok
    # The chain's middle operand converts at the native function's `int`
    # parameter and is evaluated once.
    print(n < tick() < 5, calls)  # tpyc: ok


main()

# Positions that state the type, or do not reach a protocol parameter, stay
# silent.
from typing import overload
from tpy import Stringable, int64, uint32, dispatch


def gs[T: Stringable](x: T) -> str:
    return str(x)


@overload
def ov(x: int64) -> str: ...
@overload
def ov(x: str) -> str: ...
def ov(x: int64 | str) -> str:
    return "ov"


@dispatch
def f(x: int64) -> str:
    return "int64"


@dispatch
def f(x: Stringable) -> str:
    return "Stringable"


def main() -> None:
    # a spelled type, an int64 or int conversion, a fixed-width ctor
    print("typed", str(int64(5000000000)), str(int(5000000000)))  # tpyc: ok
    print("typed", uint32(0xFFFFFFFF), str(uint32(0xFFFFFFFF)))  # tpyc: ok
    # an in-range literal
    print("range", str(100), str(2147483647), str(-2147483648))  # tpyc: ok
    # an overload won by its int64 parameter
    print("ov", ov(5000000000))  # tpyc: ok
    # print and f-string operands are not protocol parameters
    print("fstr", f"{5000000000}", 5000000000)  # tpyc: ok
    # a generic T parameter does not warn (yet), even bound by Stringable
    print("generic", gs(5000000000))  # tpyc: ok
    # a losing protocol candidate: the int64 variant wins
    print("losing", f(5000000000))  # tpyc: ok

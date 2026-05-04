# Two @overload variants with identical positional param types but
# different return types. C++ overloads on parameter shape only, so
# this can't be realized. Sema rejects up front.
from typing import overload
from tpy import Int32


@overload
def convert(x: Int32) -> Int32:
    return x * 2


@overload
def convert(x: Int32) -> str:  # tpyc: error(/identical parameter types/)
    return "v=" + str(x)


print(convert(Int32(5)))

# Two @dispatch variants with identical positional param types but
# different return types. C++ overloads on parameter shape only, so
# this can't be realized. Sema rejects up front.
from tpy import Int32, dispatch


@dispatch
def convert(x: Int32) -> Int32:
    return x * 2


@dispatch
def convert(x: Int32) -> str:  # tpyc: error(/identical parameter types/)
    return "v=" + str(x)


print(convert(Int32(5)))

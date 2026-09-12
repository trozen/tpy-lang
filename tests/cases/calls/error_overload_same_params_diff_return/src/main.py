# Two @dispatch variants with identical positional param types but
# different return types. C++ overloads on parameter shape only, so
# this can't be realized. Sema rejects up front.
from tpy import int32, dispatch


@dispatch
def convert(x: int32) -> int32:
    return x * 2


@dispatch
def convert(x: int32) -> str:  # tpyc: error(/identical parameter types/)
    return "v=" + str(x)


print(convert(int32(5)))

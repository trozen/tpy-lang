# A union impl param and a short literal stub lower, but an @error_return impl
# under @overload stubs must keep rejecting: a stub cannot re-spell the
# error-return contract, so its signature would drop the expected wrapper.
from typing import overload
from tpy import int32, error_return, ReturnException


class E(Exception, ReturnException):
    pass


@overload
def f(a: int32) -> int32: ...

@overload
def f(a: int32, b: int32) -> int32: ...

@error_return(E)
def f(a: int32, b: int32 = 0) -> int32:  # tpyc: error(/sig.overload_set.param_names/)
    return a + b


def main() -> None:
    try:
        print(f(1), f(1, 2))
    except E:
        print("e")


main()

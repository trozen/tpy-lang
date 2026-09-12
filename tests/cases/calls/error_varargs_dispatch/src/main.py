# *args on @dispatch variants is rejected, like on @overload stubs
from tpy import dispatch, int32


@dispatch
def f(*args: int32) -> int32:  # tpyc: error(/\*args is not supported on @dispatch variants/)
    return 0


@dispatch
def f(x: int32, y: int32) -> int32:
    return x + y


f(1, 2)

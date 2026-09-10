# *args on @dispatch variants is rejected, like on @overload stubs
from tpy import dispatch, Int32


@dispatch
def f(*args: Int32) -> Int32:  # tpyc: error(/\*args is not supported on @dispatch variants/)
    return 0


@dispatch
def f(x: Int32, y: Int32) -> Int32:
    return x + y


f(1, 2)

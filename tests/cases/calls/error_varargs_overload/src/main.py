# *args on @overload stubs is rejected
from typing import overload
from tpy import int32

@overload
def f(*args: int32) -> int32: ...  # tpyc: error(/overload/)

@overload
def f(x: int32, y: int32) -> int32: ...

def f(*args: int32) -> int32:
    return 0

f(1, 2)

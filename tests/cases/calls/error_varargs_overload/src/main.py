# *args on @overload stubs is rejected
from typing import overload
from tpy import Int32

@overload
def f(*args: Int32) -> Int32: ...  # tpyc: error(/overload/)

@overload
def f(x: Int32, y: Int32) -> Int32: ...

def f(*args: Int32) -> Int32:
    return 0

f(1, 2)

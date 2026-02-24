from tpy.extern import native_c
from tpy import Int32

@native_c("tpy_clock")
def get_clock() -> Int32: ...

@native_c("abs")
def my_abs(x: Int32) -> Int32: ...

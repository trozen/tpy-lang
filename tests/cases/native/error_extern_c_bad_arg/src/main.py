from tpy.extern import native_c
from tpy import Int32

@native_c(123)  # tpyc: error(/@native_c\(\) requires a str argument/)
def bad_func(x: Int32) -> Int32: ...

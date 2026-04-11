from tpy.extern import native
from tpy import Int32

@native(123)  # tpyc: error(/@native\(\) requires a str argument/)
def bad_func(x: Int32) -> Int32: ...

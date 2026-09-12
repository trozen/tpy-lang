from tpy.extern import native
from tpy import int32

@native(123)  # tpyc: error(/@native\(\) requires a str argument/)
def bad_func(x: int32) -> int32: ...

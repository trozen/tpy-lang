from tpy import native_c, Int32

@native_c("clock")
def get_clock() -> Int32: ...

@native_c("abs")
def my_abs(x: Int32) -> Int32: ...

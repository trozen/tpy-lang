from tpy import native_c, Int32

@native_c(123)  # tpyc: error(/@native_c\(\) requires a single string argument/)
def bad_func(x: Int32) -> Int32: ...

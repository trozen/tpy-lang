from tpy import native, native_c, Int32

@native
@native_c
def bad_func(x: Int32) -> Int32: ...  # tpyc: error(/cannot have both @native and @native_c/)

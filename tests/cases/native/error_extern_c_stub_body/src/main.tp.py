from tpy import extern_c, Int32

@extern_c
def bad_func(x: Int32) -> Int32: ...  # tpyc: error(/must have a body/)

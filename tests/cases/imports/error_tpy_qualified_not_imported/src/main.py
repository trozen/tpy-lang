# Test error when using tpy.int32 without 'import tpy'
def foo(x: tpy.int32) -> tpy.int32:  # tpyc: error(/requires.*import tpy/)
    return x

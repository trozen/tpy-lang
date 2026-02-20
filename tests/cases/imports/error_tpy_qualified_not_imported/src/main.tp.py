# Test error when using tpy.Int32 without 'import tpy'
def foo(x: tpy.Int32) -> tpy.Int32:  # tpyc: error(/requires.*import tpy/)
    return x

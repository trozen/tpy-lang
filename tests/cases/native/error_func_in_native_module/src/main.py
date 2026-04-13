# Test that non-native functions in native_module produce an error
# tpy: native_module
from tpy import Int32

def add(a: Int32, b: Int32) -> Int32:  # tpyc: error(/not allowed in native_module/)
    return a + b

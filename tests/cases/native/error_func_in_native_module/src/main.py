# Test that non-native functions in native_module produce an error
# tpy: native_module
from tpy import int32

def add(a: int32, b: int32) -> int32:  # tpyc: error(/not allowed in native_module/)
    return a + b

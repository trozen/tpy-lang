# Test that non-native classes in native_module produce an error
# tpy: native_module
from tpy import int32

class Point:  # tpyc: error(/not allowed in native_module/)
    x: int32
    y: int32

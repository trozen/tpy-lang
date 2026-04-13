# Test that non-native classes in native_module produce an error
# tpy: native_module
from tpy import Int32

class Point:  # tpyc: error(/not allowed in native_module/)
    x: Int32
    y: Int32

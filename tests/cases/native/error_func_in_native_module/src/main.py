# Test that non-native functions in a native module ("mod": "native") produce an error
__tpy__ = {"mod": "native"}
from tpy import int32

def add(a: int32, b: int32) -> int32:  # tpyc: error(/not allowed in a native module/)
    return a + b

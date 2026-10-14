# Test that non-native classes in a native module ("mod": "native") produce an error
__tpy__ = {"mod": "native"}
from tpy import int32

class Point:  # tpyc: error(/not allowed in a native module/)
    x: int32
    y: int32

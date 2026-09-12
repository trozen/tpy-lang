from tpy.extern import native
from tpy import int32

# C++ import with namespace — verifies namespaced call codegen
@native("my_ns::helper")
def helper(x: int32) -> int32: ...

# C++ import that calls the namespaced function above.
# Calling helper() must emit my_ns::helper() in C++.
@native
def caller(x: int32) -> int32: ...

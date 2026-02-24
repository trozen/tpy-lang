from tpy.extern import native
from tpy import Int32

# C++ import with namespace — verifies namespaced call codegen
@native("my_ns::helper")
def helper(x: Int32) -> Int32: ...

# C++ import that calls the namespaced function above.
# Calling helper() must emit my_ns::helper() in C++.
@native
def caller(x: Int32) -> Int32: ...

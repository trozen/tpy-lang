# Native module with include directive -- should be propagated to importers
# tpy: cpp_namespace("nativelib")
# tpy: include("native_ops.hpp")
from tpy import Int32
from tpy.extern import native

@native("nativelib::native_add")
def native_add(a: Int32, b: Int32) -> Int32: ...

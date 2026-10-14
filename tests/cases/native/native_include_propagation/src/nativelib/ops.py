# Native module with include directive -- should be propagated to importers
__tpy__ = {"mod": "native",
           "cpp_namespace": "nativelib",
           "includes": ["native_ops.hpp"]}
from tpy import int32
from tpy.extern import native

@native("nativelib::native_add")
def native_add(a: int32, b: int32) -> int32: ...

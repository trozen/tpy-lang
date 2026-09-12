# Native module declaring @native, @native(binding="C"), @native class, and @cpp_template
# tpy: native_module
# tpy: cpp_namespace("nativelib")
# tpy: include("native_impl.hpp")
from tpy import int32
from tpy.extern import native, cpp_template

@native
def native_func(x: int32) -> int32: ...

@native(binding="C")
def native_c_func(x: int32) -> int32: ...

@native
class NativeClass:
    value: int32

@cpp_template("({0} + {1})")
def tmpl_add(a: int32, b: int32) -> int32: ...

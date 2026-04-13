# Native module declaring @native, @native(binding="C"), @native class, and @cpp_template
# tpy: native_module
# tpy: cpp_namespace("nativelib")
# tpy: include("native_impl.hpp")
from tpy import Int32
from tpy.extern import native, cpp_template

@native
def native_func(x: Int32) -> Int32: ...

@native(binding="C")
def native_c_func(x: Int32) -> Int32: ...

@native
class NativeClass:
    value: Int32

@cpp_template("({0} + {1})")
def tmpl_add(a: Int32, b: Int32) -> Int32: ...

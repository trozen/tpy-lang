# tpy: native_module
# tpy: cpp_namespace("mylib")
# tpy: include("native_types.hpp")

from tpy.extern import native

@native("mylib::Q")
class Q:
    flag: bool

@native("mylib::A")
class A:
    q: Q

@native("mylib::S")
class S:
    a: A

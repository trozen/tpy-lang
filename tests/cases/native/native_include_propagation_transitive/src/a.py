# tpy: native_module
# tpy: cpp_namespace("xcore")
# tpy: include("<x/a.hpp>")

from tpy.extern import native

@native("xcore::B")
class B:
    flag: bool

@native("xcore::A")
class A:
    inner: B

__tpy__ = {"mod": "native",
           "cpp_namespace": "xcore",
           "includes": ["<x/a.hpp>"]}

from tpy.extern import native

@native("xcore::B")
class B:
    flag: bool

@native("xcore::A")
class A:
    inner: B

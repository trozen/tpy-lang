__tpy__ = {"mod": "native",
           "cpp_namespace": "mylib",
           "includes": ["native_types.hpp"]}

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

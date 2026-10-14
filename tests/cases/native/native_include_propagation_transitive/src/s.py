__tpy__ = {"mod": "native",
           "cpp_namespace": "xcore",
           "includes": ["<x/s.hpp>"]}

from tpy.extern import native
from a import A

@native("xcore::S")
class S:
    @native("outer")
    @property
    def outer(self) -> A: ...

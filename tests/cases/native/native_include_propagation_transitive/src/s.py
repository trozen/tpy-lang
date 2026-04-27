# tpy: native_module
# tpy: cpp_namespace("xcore")
# tpy: include("<x/s.hpp>")

from tpy.extern import native
from a import A

@native("xcore::S")
class S:
    @native("outer")
    @property
    def outer(self) -> A: ...

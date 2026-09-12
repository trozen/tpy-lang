# tpy: native_module
# tpy: cpp_namespace("xcore")
# tpy: include("<x/s.hpp>")
from tpy.extern import native
from tpy import int32


@native("xcore::S2")
class S2:
    @native("pick")
    def pick(self, i: int32) -> int32: ...

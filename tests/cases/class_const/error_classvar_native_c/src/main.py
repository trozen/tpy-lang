# Error: @native_c records (C structs) have no static members.
# tpy: include("native_types.hpp")
from tpy.extern import native
from typing import ClassVar
from tpy import Int32


@native("BuildOpts", binding="C")
class BuildOpts:
    counter: ClassVar[Int32] = 0  # tpyc: error(/`@native_c` classes have no static members/)

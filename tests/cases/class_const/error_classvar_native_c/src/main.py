# Error: @native_c records (C structs) have no static members.
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy.extern import native
from typing import ClassVar
from tpy import int32


@native("BuildOpts", binding="C")
class BuildOpts:
    counter: ClassVar[int32] = 0  # tpyc: error(/`@native_c` classes have no static members/)

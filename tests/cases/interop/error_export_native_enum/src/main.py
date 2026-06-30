# A @native enum maps to a C++ enum whose values come from that side, so it has
# no TPy-declared member table to rebuild as a CPython enum -- @export on it is
# rejected (only tpy-defined enums can be exposed).
# tpy: ext_module
# tpy: include("native_types.hpp")
from enum import Enum
from tpy import Int8
from tpy.extern import native, export


@native("ns::color_t")
@export
class Color(Int8, Enum):  # tpyc: error(/@native enum cannot be exposed/)
    RED = 1
    GREEN = 2

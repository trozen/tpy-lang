# An `@export` enum is recreated on the CPython side as a bare IntEnum/Enum,
# so its TPy methods would be missing there: rejected rather than dropped.
# tpy: ext_module
from enum import Enum
from tpy.extern import export


@export
class Color(Enum):  # tpyc: error(/an enum with methods cannot be exposed to CPython yet/)
    Red = 0
    Blue = 1

    def label(self) -> str:
        return "x"

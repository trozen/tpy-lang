# A foreign aliased enum colliding with a local same-named exposed enum
# must still be rejected as cross-module, not treated as local.
# tpy: ext_module
from enum import IntEnum
from tpy.extern import export
from enum_mod import Color as ForeignColor


@export
class Color(IntEnum):
    BLUE = 1


@export
def step(c: ForeignColor) -> ForeignColor:  # tpyc: error(/is an exposed enum from another module/)
    return c

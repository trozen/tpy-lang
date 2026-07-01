# An exposed enum/class is a valid top-level @export boundary type but not yet
# a container ELEMENT: the per-element converter has no module type handle to
# round-trip a member/instance through, so it is rejected with the
# unmarshallable diagnostic.
# tpy: ext_module
from enum import IntEnum
from tpy.extern import export


@export
class Color(IntEnum):
    RED = 1
    GREEN = 2


@export
def f(xs: list[Color]) -> int:  # tpyc: error(/parameter 'xs'.*not yet marshallable/)
    return len(xs)

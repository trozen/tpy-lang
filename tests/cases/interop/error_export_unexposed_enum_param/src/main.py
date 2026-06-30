# A non-@export enum has no CPython type to round-trip a value through, so it is
# not a valid @export param/return type -- only exposed enums cross.
# tpy: ext_module
from enum import IntEnum
from tpy.extern import export


class Color(IntEnum):
    RED = 1
    GREEN = 2


@export
def step(c: Color) -> Color:  # tpyc: error(/not yet marshallable/)
    return c

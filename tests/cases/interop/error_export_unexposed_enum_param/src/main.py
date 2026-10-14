# A non-@export enum has no CPython type to round-trip a value through, so it is
# not a valid @export param/return type -- only exposed enums cross.
__tpy__ = {"mod": "ext"}
from enum import IntEnum
from tpy.extern import export


class Color(IntEnum):
    RED = 1
    GREEN = 2


@export
def step(c: Color) -> Color:  # tpyc: error(/cannot cross the CPython boundary/)
    return c

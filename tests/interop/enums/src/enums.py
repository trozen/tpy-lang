# tpy: ext_module
# Exposes TPy enums as real CPython enum types. An @export IntEnum / Enum is
# recreated at PyInit_ via the stdlib `enum` functional API (module= set), so
# members, .name/.value, identity, iteration, value/name lookup, and the
# IntEnum-vs-Enum int-comparison distinction all match a source-level
# class-statement enum (the cpy-parity run defines exactly that).
from enum import Enum, IntEnum
from tpy.extern import export


@export
class Color(IntEnum):
    INVALID = -1  # negative member: exercises the signed static_cast<underlying>
    RED = 1
    GREEN = 2
    BLUE = 4


@export
class Direction(Enum):
    NORTH = 0
    EAST = 1
    SOUTH = 2
    WEST = 3

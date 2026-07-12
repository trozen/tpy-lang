# tpy: ext_module
# Marshals enum VALUES across the @export boundary: an exposed enum used as a
# function (or method) param/return crosses as its CPython member. The value
# round-trips through the module's enum type, so member identity is preserved
# (Color(v) returns the singleton). Covers IntEnum + plain Enum, a negative-
# valued member, a UInt64 member above INT64_MAX (must cross unsigned), and a
# method param/return. A non-member arg is rejected (strict by-type) -- ext_checks.
from enum import Enum, IntEnum
from tpy import Int64, UInt64
from tpy.extern import export


@export
class Color(IntEnum):
    INVALID = -1
    RED = 1
    GREEN = 2


@export
class Shape(Enum):
    CIRCLE = 1
    SQUARE = 2


@export
class Big(UInt64, Enum):
    LOW = 1
    HIGH = 0x8000000000000000  # 2**63, above INT64_MAX -- must not wrap negative


@export
def next_color(c: Color) -> Color:
    if c == Color.RED:
        return Color.GREEN
    if c == Color.GREEN:
        return Color.RED
    return Color.INVALID


@export
def flip(s: Shape) -> Shape:
    return Shape.SQUARE if s == Shape.CIRCLE else Shape.CIRCLE


@export
def echo_big(b: Big) -> Big:
    return b


# Asymmetric signatures: exercise the marshal-IN side alone (enum param, scalar
# return) and the marshal-OUT side alone (no enum param, enum return).
@export
def is_red(c: Color) -> bool:
    return c == Color.RED


@export
def default_color() -> Color:
    return Color.GREEN


@export
class Toggle:
    def __init__(self, n: Int64):
        self.n = n

    def pick(self, c: Color) -> Color:
        return Color.GREEN if c == Color.RED else Color.RED

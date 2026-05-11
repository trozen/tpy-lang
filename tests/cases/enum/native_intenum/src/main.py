# @native IntEnum with explicit underlying type (Int8) matching the C++ side.
# tpy: include("native_types.hpp")
from enum import Enum, auto
from tpy import Int8
from tpy.extern import native


@native("ns::dir_t")
class Direction(Int8, Enum):
    UP = auto()
    DOWN = auto()


def desc(d: Direction) -> str:
    match d:
        case Direction.UP:
            return "rising"
        case Direction.DOWN:
            return "falling"


def main() -> None:
    d = Direction.UP
    print(d)
    print(desc(d))
    print(desc(Direction.DOWN))
    print(d.value)


main()

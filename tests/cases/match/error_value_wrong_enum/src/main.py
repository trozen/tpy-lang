# error: value pattern enum type doesn't match subject enum type
from enum import Enum, auto

class Color(Enum):
    RED = auto()
    GREEN = auto()

class Direction(Enum):
    NORTH = auto()
    SOUTH = auto()

def describe(c: Color) -> str:
    match c:
        case Direction.NORTH:  # tpyc: error(/does not match/)
            return "north"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()

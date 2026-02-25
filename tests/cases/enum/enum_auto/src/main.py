# Enum with auto() values (start at 1, matching CPython)
from enum import Enum, auto

class Direction(Enum):
    North = auto()
    South = auto()
    East = auto()
    West = auto()

def main() -> None:
    d: Direction = Direction.North
    print(d)
    print(d.value)
    d = Direction.West
    print(d)
    print(d.value)

main()

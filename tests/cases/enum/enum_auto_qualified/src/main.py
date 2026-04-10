# enum.auto() via bare 'import enum' (qualified access)
import enum

class Direction(enum.Enum):
    North = enum.auto()
    South = enum.auto()
    East = enum.auto()
    West = enum.auto()

def main() -> None:
    d: Direction = Direction.North
    print(d)
    print(d.value)
    d = Direction.West
    print(d)
    print(d.value)

main()

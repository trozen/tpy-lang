# Error: comparing different enum types
from enum import Enum

class Color(Enum):
    Red = 0

class Direction(Enum):
    North = 0

def main() -> None:
    c: Color = Color.Red
    d: Direction = Direction.North
    print(c == d)  # tpyc: error(/Cannot compare enum types/)

main()

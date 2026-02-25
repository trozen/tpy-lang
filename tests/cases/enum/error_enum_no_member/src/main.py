# Error: accessing non-existent enum member
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1

def main() -> None:
    c: Color = Color.Yellow  # tpyc: error(/has no member 'Yellow'/)

main()

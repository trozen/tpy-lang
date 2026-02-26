# Test enum value lookup panic on invalid value
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def main() -> None:
    c: Color = Color(99)
    print(c)

main()

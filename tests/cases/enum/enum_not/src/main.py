# not operator on enum values (all enums are truthy, so not is always False)
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def main() -> None:
    c: Color = Color.Red
    print(not c)
    print(not Color.Green)
    x: bool = not Color.Blue
    print(x)

main()

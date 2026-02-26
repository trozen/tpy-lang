# Test enum name lookup via subscript: Color["Red"]
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def lookup(name: str) -> Color:
    return Color[name]

def main() -> None:
    print(Color["Red"])
    print(Color["Green"])
    print(Color["Blue"])
    print(lookup("Green"))

main()

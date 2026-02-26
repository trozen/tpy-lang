# Error: calling a nonexistent method on an enum type
from enum import Enum

class Color(Enum):
    Red = 0

def main() -> None:
    c = Color.some_method()  # tpyc: error(/has no method/)

main()

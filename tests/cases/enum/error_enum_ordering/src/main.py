# Error: ordering operators on enum
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1

def main() -> None:
    a: Color = Color.Red
    b: Color = Color.Green
    print(a < b)  # tpyc: error(/Ordering operators not supported/)

main()

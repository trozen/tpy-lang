# Error: comparing enum with integer
from enum import Enum
from tpy import Int32

class Color(Enum):
    Red = 0

def main() -> None:
    c: Color = Color.Red
    x: Int32 = Int32(0)
    print(c == x)  # tpyc: error(/Cannot compare/)

main()

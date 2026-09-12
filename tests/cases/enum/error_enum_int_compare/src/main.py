# Error: comparing enum with integer
from enum import Enum
from tpy import int32

class Color(Enum):
    Red = 0

def main() -> None:
    c: Color = Color.Red
    x: int32 = int32(0)
    print(c == x)  # tpyc: error(/Cannot compare/)

main()

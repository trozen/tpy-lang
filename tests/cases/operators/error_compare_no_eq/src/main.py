# Test error when comparing user records without __eq__
from tpy import Int32

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    a = Point(Int32(1), Int32(2))
    b = Point(Int32(1), Int32(2))
    print(a == b)  # tpyc: error(/no '__eq__'/)
    print(a < b)   # tpyc: error(/no '__lt__'/)

main()

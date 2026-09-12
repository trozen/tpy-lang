# Test error when comparing user records without __eq__
from tpy import int32

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    a = Point(int32(1), int32(2))
    b = Point(int32(1), int32(2))
    print(a == b)  # tpyc: error(/no '__eq__'/)
    print(a < b)   # tpyc: error(/no '__lt__'/)

main()

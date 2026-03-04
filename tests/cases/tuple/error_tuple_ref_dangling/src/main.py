# Return tuple with reference to local -- dangling reference error
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def bad() -> tuple[Int32, Point]:
    local = Point(Int32(1), Int32(2))
    return (Int32(0), local)  # tpyc: error(/Cannot return local.*tuple element/)

def main() -> None:
    pass

main()

# Return tuple with reference to local -- dangling reference error
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def bad() -> tuple[int32, Point]:
    local = Point(int32(1), int32(2))
    return (int32(0), local)  # tpyc: error(/Cannot return local.*tuple element/)

def main() -> None:
    pass

main()

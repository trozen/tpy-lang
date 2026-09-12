from tpy import int32, Ptr

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p: Ptr[Point] = Point(10, 20)  # tpyc: error(/Cannot take mutable pointer to read-only or temporary/)

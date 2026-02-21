from tpy import Int32, Ptr

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p: Ptr[Point] = Point(10, 20)  # tpyc: error(/Cannot take mutable pointer to read-only or temporary/)

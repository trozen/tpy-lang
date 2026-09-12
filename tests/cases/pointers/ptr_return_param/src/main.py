from tpy import int32, Ptr

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

global_pt: Point = Point(1, 2)

def addr_param(p: Point) -> Ptr[Point]:
    # Returning pointer to parameter should be allowed (safe lifetime)
    return p

def addr_global() -> Ptr[Point]:
    # Returning pointer to global should be allowed
    return global_pt

def main() -> None:
    local: Point = Point(10, 20)
    p1: Ptr[Point] = addr_param(local)
    print(p1.x)  # tpyc: nullable(p1)
    p2: Ptr[Point] = addr_global()
    print(p2.y)  # tpyc: nullable(p2)

main()

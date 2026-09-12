from tpy import int32, Own

# Test: copy() requires explicit import from tpy

class Point:
    x: int32
    y: int32

def make_point() -> Own[Point]:
    p: Point = Point()
    return copy(p)  # tpyc: error(/Unknown function.*copy/)

def main() -> None:
    pass

main()

from tpy import Int32, Own

# Test: copy() requires explicit import from tpy

class Point:
    x: Int32
    y: Int32

def make_point() -> Own[Point]:
    p: Point = Point()
    return copy(p)  # tpyc: error(/Unknown function.*copy/)

def main() -> None:
    pass

main()

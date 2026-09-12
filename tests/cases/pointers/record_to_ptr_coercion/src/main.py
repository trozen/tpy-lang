from tpy import int32, Ptr, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def modify_point(p: Ptr[Point]) -> None:
    p.x = 999

def read_point(p: Ptr[readonly[Point]]) -> int32:
    return p.x

def test_coercion() -> None:
    pt: Point = Point(10, 20)

    # Record -> Ptr coercion in function call
    modify_point(pt)
    print(pt.x)  # Should print 999

    # Record -> Ptr[readonly[...]] coercion in function call
    result: int32 = read_point(pt)
    print(result)  # Should print 999

    # Explicit Ptr -> Ptr[readonly[...]] also works
    ptr: Ptr[Point] = pt
    result2: int32 = read_point(ptr)
    print(result2)  # Should print 999

# Run test
test_coercion()

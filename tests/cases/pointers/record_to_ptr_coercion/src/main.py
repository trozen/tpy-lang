from tpy import Int32, Ptr, ReadOnlyPtr

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def modify_point(p: Ptr[Point]) -> None:
    p.x = 999

def read_point(p: ReadOnlyPtr[Point]) -> Int32:
    return p.x

def test_coercion() -> None:
    pt: Point = Point(10, 20)

    # Record -> Ptr coercion in function call
    modify_point(pt)
    print(pt.x)  # Should print 999

    # Record -> ReadOnlyPtr coercion in function call
    result: Int32 = read_point(pt)
    print(result)  # Should print 999

    # Explicit Ptr -> ReadOnlyPtr also works
    ptr: Ptr[Point] = pt
    result2: Int32 = read_point(ptr)
    print(result2)  # Should print 999

# Run test
test_coercion()

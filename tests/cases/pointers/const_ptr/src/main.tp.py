from tpy import Int32, Ptr, ConstPtr

# Test ConstPtr[T] type for read-only pointers

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def read_point(p: ConstPtr[Point]) -> Int32:
    """Read from a const pointer - should work."""
    return p.x + p.y

def modify_via_ptr(p: Ptr[Point], new_x: Int32) -> None:
    """Modify via mutable pointer."""
    p.x = new_x

def test_ptr_to_const_ptr() -> None:
    """Test Ptr to ConstPtr conversion."""
    pt: Point = Point(10, 20)

    # Get mutable pointer
    mp: Ptr[Point] = pt

    # Read via mutable pointer
    print(mp.x)
    print(mp.y)

    # Modify through mutable pointer
    modify_via_ptr(mp, 100)
    print(pt.x)

    # Convert mutable pointer to const pointer (should be allowed)
    cp: ConstPtr[Point] = mp

    # Read through const pointer
    total: Int32 = read_point(cp)
    print(total)

def test_const_ptr_preserves_value() -> None:
    """Test that const pointer sees updates to underlying value."""
    pt: Point = Point(1, 2)
    mp: Ptr[Point] = pt
    cp: ConstPtr[Point] = mp

    print(cp.x)

    # Modify original via mutable pointer
    mp.x = 999
    print(cp.x)

# Run tests
print("=== ptr to const ===")
test_ptr_to_const_ptr()
print("=== preserves value ===")
test_const_ptr_preserves_value()

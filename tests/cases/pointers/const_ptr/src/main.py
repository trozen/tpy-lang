from tpy import int32, Ptr, readonly

# Test Ptr[readonly[T]] type for read-only pointers

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def read_point(p: Ptr[readonly[Point]]) -> int32:
    """Read from a const pointer - should work."""
    return p.x + p.y

def modify_via_ptr(p: Ptr[Point], new_x: int32) -> None:
    """Modify via mutable pointer."""
    p.x = new_x

def test_ptr_to_const_ptr() -> None:
    """Test Ptr to Ptr[readonly[...]] conversion."""
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
    cp: Ptr[readonly[Point]] = mp

    # Read through const pointer
    total: int32 = read_point(cp)
    print(total)

def test_const_ptr_preserves_value() -> None:
    """Test that const pointer sees updates to underlying value."""
    pt: Point = Point(1, 2)
    mp: Ptr[Point] = pt
    cp: Ptr[readonly[Point]] = mp

    print(cp.x)

    # Modify original via mutable pointer
    mp.x = 999
    print(cp.x)

# Run tests
print("=== ptr to const ===")
test_ptr_to_const_ptr()
print("=== preserves value ===")
test_const_ptr_preserves_value()

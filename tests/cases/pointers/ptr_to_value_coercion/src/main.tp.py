from tpy import Int32, Ptr

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def print_point(p: Point) -> None:
    print(p.x)
    print(p.y)

def get_sum(p: Point) -> Int32:
    return p.x + p.y

def modify_point(p: Point) -> None:
    p.x = 999

def deref_and_return(ptr: Ptr[Point]) -> Point:
    # Ptr -> Point coercion in return statement
    return ptr

def test_ptr_to_value() -> None:
    pt: Point = Point(10, 20)
    ptr: Ptr[Point] = pt

    # Ptr[Point] -> Point coercion in function call
    print_point(ptr)

    result: Int32 = get_sum(ptr)
    print(result)

    # Modification through coerced pointer affects original
    modify_point(ptr)
    print(pt.x)

def test_ptr_to_value_assign() -> None:
    pt: Point = Point(5, 7)
    ptr: Ptr[Point] = pt

    # Ptr[Point] -> Point coercion in assignment
    p2: Point = ptr
    print(p2.x)
    print(p2.y)

def test_ptr_to_value_return() -> None:
    pt: Point = Point(100, 200)
    ptr: Ptr[Point] = pt

    # Ptr -> Point coercion in return
    p2: Point = deref_and_return(ptr)
    print(p2.x)

# Run tests
print("=== call ===")
test_ptr_to_value()
print("=== assign ===")
test_ptr_to_value_assign()
print("=== return ===")
test_ptr_to_value_return()

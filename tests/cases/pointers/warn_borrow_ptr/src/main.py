# Warn on mutation of storage while a pointer borrows into it
from tpy import int32, Ptr, take_ptr

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def test_ptr_borrow_field_write() -> None:
    """Ptr borrow + field write on storage = warn."""
    p = Point(int32(1), int32(2))
    ptr = take_ptr(p)
    p.x = int32(10)  # tpyc: warning(/Mutation of 'p'.*field assignment/)
    print(p.x)

def test_ptr_borrow_append() -> None:
    """Ptr into list element + structural mutation = warn."""
    items: list[Point] = [Point(int32(1), int32(2))]
    ptr = take_ptr(items[int32(0)])
    items.append(Point(int32(3), int32(4)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

def test_ptr_borrow_subscript_assign() -> None:
    """Ptr into list element + subscript assign = ok (in-place, no reallocation, no dangling)."""
    items: list[Point] = [Point(int32(1), int32(2))]
    ptr = take_ptr(items[int32(0)])
    items[int32(0)] = Point(int32(9), int32(9))  # tpyc: ok
    print(items[int32(0)].x)

def test_ptr_reassign_clears() -> None:
    """Reassigning the ptr variable clears the borrow."""
    p = Point(int32(1), int32(2))
    q = Point(int32(3), int32(4))
    ptr = take_ptr(p)
    ptr = take_ptr(q)
    p.x = int32(10)  # tpyc: ok
    print(p.x)

test_ptr_borrow_field_write()
test_ptr_borrow_append()
test_ptr_borrow_subscript_assign()
test_ptr_reassign_clears()

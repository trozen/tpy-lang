# Warn on mutation of storage while a pointer borrows into it
from tpy import Int32, Ptr

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def test_ptr_borrow_field_write() -> None:
    """Ptr borrow + field write on storage = warn."""
    p = Point(Int32(1), Int32(2))
    ptr = Ptr(p)
    p.x = Int32(10)  # tpyc: warning(/Mutation of 'p'.*field assignment/)
    print(p.x)

def test_ptr_borrow_append() -> None:
    """Ptr into list element + structural mutation = warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    ptr = Ptr(items[Int32(0)])
    items.append(Point(Int32(3), Int32(4)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

def test_ptr_borrow_subscript_assign() -> None:
    """Ptr into list element + subscript assign = warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    ptr = Ptr(items[Int32(0)])
    items[Int32(0)] = Point(Int32(9), Int32(9))  # tpyc: warning(/Mutation of 'items'.*subscript/)
    print(items[Int32(0)].x)

def test_ptr_reassign_clears() -> None:
    """Reassigning the ptr variable clears the borrow."""
    p = Point(Int32(1), Int32(2))
    q = Point(Int32(3), Int32(4))
    ptr = Ptr(p)
    ptr = Ptr(q)
    p.x = Int32(10)  # tpyc: ok
    print(p.x)

test_ptr_borrow_field_write()
test_ptr_borrow_append()
test_ptr_borrow_subscript_assign()
test_ptr_reassign_clears()

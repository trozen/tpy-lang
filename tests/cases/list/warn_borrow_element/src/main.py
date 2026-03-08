# Warn on container mutation while element/alias borrows are active
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def test_element_borrow_append() -> None:
    """Element borrow + structural mutation = warn."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    v = items[Int32(0)]
    items.append(Point(Int32(5), Int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

def test_element_borrow_subscript_assign() -> None:
    """Element borrow + subscript assign = warn (overwrites referenced element)."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    v = items[Int32(0)]
    items[Int32(0)] = Point(Int32(9), Int32(9))  # tpyc: warning(/Mutation of 'items'.*subscript/)
    print(items[Int32(0)].x)

def test_alias_no_warn() -> None:
    """Whole-container alias + structural mutation = no warn (alias is safe)."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    alias = items
    items.append(Point(Int32(3), Int32(4)))  # tpyc: ok
    print(len(alias))

def test_value_type_no_borrow() -> None:
    """Subscript of value type does not create an element borrow."""
    items: list[Int32] = [Int32(1), Int32(2), Int32(3)]
    v = items[Int32(0)]
    items.append(Int32(4))  # tpyc: ok
    print(v)

def test_reassign_clears_borrows() -> None:
    """Reassigning storage clears borrows -- no false positive."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    items = [Point(Int32(3), Int32(4))]
    items.append(Point(Int32(5), Int32(6)))  # tpyc: ok
    print(len(items))

def test_reassign_borrower_clears() -> None:
    """Reassigning the borrower clears its borrow."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    v = items[Int32(0)]
    v = Point(Int32(9), Int32(9))
    items.append(Point(Int32(5), Int32(6)))  # tpyc: ok
    print(v.x)

def test_field_borrow_write() -> None:
    """Field write on an object with alias borrow (not field borrow) = ok."""
    p = Point(Int32(1), Int32(2))
    ref = p
    p.x = Int32(10)  # tpyc: ok (alias borrow, not field borrow)
    print(ref.x)

test_element_borrow_append()
test_element_borrow_subscript_assign()
test_alias_no_warn()
test_value_type_no_borrow()
test_reassign_clears_borrows()
test_reassign_borrower_clears()
test_field_borrow_write()

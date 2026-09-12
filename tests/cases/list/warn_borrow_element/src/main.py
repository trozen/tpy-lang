# Warn on container mutation while element/alias borrows are active
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def test_element_borrow_append() -> None:
    """Element borrow + structural mutation = warn."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    v = items[int32(0)]
    items.append(Point(int32(5), int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

def test_element_borrow_subscript_assign() -> None:
    """Element borrow + subscript assign = ok (in-place write, no reallocation, no dangling)."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    v = items[int32(0)]
    items[int32(0)] = Point(int32(9), int32(9))  # tpyc: ok
    print(items[int32(0)].x)

def test_alias_no_warn() -> None:
    """Whole-container alias + structural mutation = no warn (alias is safe)."""
    items: list[Point] = [Point(int32(1), int32(2))]
    alias = items
    items.append(Point(int32(3), int32(4)))  # tpyc: ok
    print(len(alias))

def test_value_type_no_borrow() -> None:
    """Subscript of value type does not create an element borrow."""
    items: list[int32] = [int32(1), int32(2), int32(3)]
    v = items[int32(0)]
    items.append(int32(4))  # tpyc: ok
    print(v)

def test_reassign_clears_borrows() -> None:
    """Reassigning storage clears borrows -- no false positive."""
    items: list[Point] = [Point(int32(1), int32(2))]
    v = items[int32(0)]
    items = [Point(int32(3), int32(4))]
    items.append(Point(int32(5), int32(6)))  # tpyc: ok
    print(len(items))

def test_reassign_borrower_clears() -> None:
    """Reassigning the borrower clears its borrow."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    v = items[int32(0)]
    v = Point(int32(9), int32(9))
    items.append(Point(int32(5), int32(6)))  # tpyc: ok
    print(v.x)

def test_element_borrow_del() -> None:
    """Element borrow + del = warn (del removes an element, may shift references)."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    v = items[int32(0)]
    del items[int32(0)]  # tpyc: warning(/'del' may invalidate references/)
    print(len(items))

def test_field_borrow_write() -> None:
    """Field write on an object with alias borrow (not field borrow) = ok."""
    p = Point(int32(1), int32(2))
    ref = p
    p.x = int32(10)  # tpyc: ok (alias borrow, not field borrow)
    print(ref.x)

test_element_borrow_append()
test_element_borrow_subscript_assign()
test_alias_no_warn()
test_value_type_no_borrow()
test_reassign_clears_borrows()
test_reassign_borrower_clears()
test_element_borrow_del()
test_field_borrow_write()

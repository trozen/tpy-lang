# Warn on list/dict/set += when element borrows are active (structural mutation, may reallocate)
from tpy import Int32

class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x

def test_list_aug_assign() -> None:
    items: list[Point] = [Point(Int32(1)), Point(Int32(2))]
    v = items[Int32(0)]
    # Mutate through the borrow before the aug-assign: 50 proves `v` aliases
    # items[0]; a silent copy would leave it at 1.
    v.x = 50
    print(items[Int32(0)].x)
    items += [Point(Int32(3))]  # tpyc: warning(/Mutation of 'items'.*'\+='/)
    print(len(items))

def test_list_method_still_warns() -> None:
    """Existing method-call warning not broken."""
    items: list[Point] = [Point(Int32(1))]
    v = items[Int32(0)]
    v.x = 60
    print(items[Int32(0)].x)
    items.append(Point(Int32(2)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

def test_value_type_no_warn() -> None:
    """Value-type element: no borrow, no warning."""
    items: list[Int32] = [Int32(1), Int32(2)]
    v = items[Int32(0)]
    items += [Int32(3)]  # tpyc: ok
    print(len(items))

def test_iter_borrow_aug_assign() -> None:
    """ITER borrow (for loop) + aug-assign = 'while iterating' warning."""
    items: list[Point] = [Point(Int32(1)), Point(Int32(2))]
    for p in items:
        items += [Point(Int32(3))]  # tpyc: warning(/Mutation of 'items'.*'\+=' invalidates the iterator/)
        break

def test_reassign_borrower_clears() -> None:
    """Reassigning borrower clears borrow -- no false positive."""
    items: list[Point] = [Point(Int32(1))]
    v = items[Int32(0)]
    v = Point(Int32(9))
    items += [Point(Int32(2))]  # tpyc: ok
    print(len(items))

test_list_aug_assign()
test_list_method_still_warns()
test_value_type_no_warn()
test_iter_borrow_aug_assign()
test_reassign_borrower_clears()

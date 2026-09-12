# Warn on list/dict/set += when element borrows are active (structural mutation, may reallocate)
from tpy import int32

class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def test_list_aug_assign() -> None:
    items: list[Point] = [Point(int32(1)), Point(int32(2))]
    v = items[int32(0)]
    # Mutate through the borrow before the aug-assign: 50 proves `v` aliases
    # items[0]; a silent copy would leave it at 1.
    v.x = 50
    print(items[int32(0)].x)
    items += [Point(int32(3))]  # tpyc: warning(/Mutation of 'items'.*'\+='/)
    print(len(items))

def test_list_method_still_warns() -> None:
    """Existing method-call warning not broken."""
    items: list[Point] = [Point(int32(1))]
    v = items[int32(0)]
    v.x = 60
    print(items[int32(0)].x)
    items.append(Point(int32(2)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

def test_value_type_no_warn() -> None:
    """Value-type element: no borrow, no warning."""
    items: list[int32] = [int32(1), int32(2)]
    v = items[int32(0)]
    items += [int32(3)]  # tpyc: ok
    print(len(items))

def test_iter_borrow_aug_assign() -> None:
    """ITER borrow (for loop) + aug-assign = 'while iterating' warning."""
    items: list[Point] = [Point(int32(1)), Point(int32(2))]
    for p in items:
        items += [Point(int32(3))]  # tpyc: warning(/Mutation of 'items'.*'\+=' invalidates the iterator/)
        break

def test_reassign_borrower_clears() -> None:
    """Reassigning borrower clears borrow -- no false positive."""
    items: list[Point] = [Point(int32(1))]
    v = items[int32(0)]
    v = Point(int32(9))
    items += [Point(int32(2))]  # tpyc: ok
    print(len(items))

test_list_aug_assign()
test_list_method_still_warns()
test_value_type_no_warn()
test_iter_borrow_aug_assign()
test_reassign_borrower_clears()

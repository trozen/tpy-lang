# 8b gaps: (a) reassignment, (b) call iterables, (c) transitive return inference.
from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def get_first(items: list[Point]) -> Point:
    return items[0]  # return_borrows_from = {0}


def get_list(items: list[Point]) -> list[Point]:
    return items  # return_borrows_from = {0}


# --- Gap (a): reassignment ---

def test_reassign_then_mutate_warns() -> None:
    """Reassigning x from a call result registers the borrow; append warns."""
    items = [Point(1, 2), Point(3, 4)]
    x = Point(0, 0)             # initial assignment (no borrow from items)
    x = get_first(items)        # reassignment: borrow from items registered here
    items.append(Point(5, 6))   # tpyc: warning(/Mutation of 'items' while borrowed/)
    print(len(items))           # 3 (don't dereference x after potential realloc)


def test_reassign_no_mutation_ok() -> None:
    """Reassigning x from a call result is fine if items is not structurally mutated."""
    items = [Point(1, 2), Point(3, 4)]
    x = Point(0, 0)
    x = get_first(items)        # tpyc: ok
    print(x.x)                  # 1


def test_reassign_overwrite_clears_borrow() -> None:
    """Reassigning x again (not from items) clears the borrow; no warning."""
    items = [Point(1, 2), Point(3, 4)]
    other = [Point(9, 9)]
    x = Point(0, 0)
    x = get_first(items)        # borrows items
    x = get_first(other)        # rebinds x to other; clears borrow on items
    items.append(Point(5, 6))   # tpyc: ok (x no longer borrows items)
    print(x.x)                  # 9


# --- Gap (b): call iterable ---

def test_for_call_iterable_warns() -> None:
    """for-loop over a call that borrows from items: append inside loop warns."""
    items = [Point(1, 2), Point(3, 4)]
    for p in get_list(items):   # ITER borrow registered on items via return_borrows_from
        items.append(Point(9, 9))  # tpyc: warning(/Mutation of 'items' while iterating/)
        break
    print(len(items))           # 3


def test_for_call_iterable_readonly_ok() -> None:
    """for-loop over a call result is fine if the body doesn't mutate the source."""
    items = [Point(1, 2), Point(3, 4)]
    total = Int32(0)
    for p in get_list(items):   # tpyc: ok
        total = total + p.x
    print(total)                # 4 (1+3)


# --- Gap (c): transitive return inference ---

def get_first_wrapper(items: list[Point]) -> Point:
    """Returns the result of a call with return_borrows_from -- should propagate."""
    return get_first(items)  # transitive: return_borrows_from = {0} via get_first


def test_transitive_return_warns() -> None:
    """Wrapper that returns a borrow: call site should register the borrow."""
    items = [Point(1, 2), Point(3, 4)]
    x = get_first_wrapper(items)
    items.append(Point(5, 6))   # tpyc: warning(/Mutation of 'items' while borrowed/)
    print(len(items))           # 3


def test_transitive_return_no_mutation_ok() -> None:
    """Wrapper that returns a borrow; no mutation means no warning."""
    items = [Point(1, 2), Point(3, 4)]
    x = get_first_wrapper(items)    # tpyc: ok
    print(x.x)                      # 1


def main() -> None:
    test_reassign_then_mutate_warns()
    test_reassign_no_mutation_ok()
    test_reassign_overwrite_clears_borrow()
    test_for_call_iterable_warns()
    test_for_call_iterable_readonly_ok()
    test_transitive_return_warns()
    test_transitive_return_no_mutation_ok()


main()

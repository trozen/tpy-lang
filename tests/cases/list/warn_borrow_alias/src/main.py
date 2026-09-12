# Warn when mutating through an alias of a container with element borrows
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def mutate_list(items: list[Point]) -> None:
    items.append(Point(int32(9), int32(9)))

def test_alias_append() -> None:
    """Mutation through alias of borrowed container = warn."""
    items: list[Point] = [Point(int32(1), int32(2))]
    v = items[int32(0)]
    alias = items
    alias.append(Point(int32(5), int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

def test_alias_subscript_assign() -> None:
    """Subscript assign through alias = ok (in-place, no reallocation, no dangling)."""
    items: list[Point] = [Point(int32(1), int32(2))]
    v = items[int32(0)]
    alias = items
    alias[int32(0)] = Point(int32(9), int32(9))  # tpyc: ok
    print(items[int32(0)].x)

def test_alias_del() -> None:
    """Del through alias = warn."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    v = items[int32(0)]
    alias = items
    del alias[int32(1)]  # tpyc: warning(/Mutation of 'items'.*'del'/)
    print(len(items))

def test_alias_pass_to_func() -> None:
    """Passing alias of borrowed container to function = warn."""
    items: list[Point] = [Point(int32(1), int32(2))]
    v = items[int32(0)]
    alias = items
    mutate_list(alias)  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

def test_alias_aug_assign() -> None:
    """Aug-assign through alias of borrowed container = warn with root storage name."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    v = items[int32(0)]
    alias = items
    alias += [Point(int32(5), int32(6))]  # tpyc: warning(/Mutation of 'items'.*'\+='/)
    print(len(items))

def test_alias_no_element_borrow() -> None:
    """Alias without element borrows = no warn."""
    items: list[Point] = [Point(int32(1), int32(2))]
    alias = items
    alias.append(Point(int32(3), int32(4)))  # tpyc: ok
    print(len(items))

def test_direct_still_works() -> None:
    """Direct mutation of borrowed container still warns."""
    items: list[Point] = [Point(int32(1), int32(2))]
    v = items[int32(0)]
    items.append(Point(int32(5), int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

test_alias_append()
test_alias_subscript_assign()
test_alias_del()
test_alias_pass_to_func()
test_alias_aug_assign()
test_alias_no_element_borrow()
test_direct_still_works()

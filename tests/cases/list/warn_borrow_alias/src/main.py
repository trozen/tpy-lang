# Warn when mutating through an alias of a container with element borrows
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def mutate_list(items: list[Point]) -> None:
    items.append(Point(Int32(9), Int32(9)))

def test_alias_append() -> None:
    """Mutation through alias of borrowed container = warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    alias = items
    alias.append(Point(Int32(5), Int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

def test_alias_subscript_assign() -> None:
    """Subscript assign through alias = warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    alias = items
    alias[Int32(0)] = Point(Int32(9), Int32(9))  # tpyc: warning(/Mutation of 'items'.*subscript/)
    print(items[Int32(0)].x)

def test_alias_del() -> None:
    """Del through alias = warn."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    v = items[Int32(0)]
    alias = items
    del alias[Int32(1)]  # tpyc: warning(/Mutation of 'items'.*'del'/)
    print(len(items))

def test_alias_pass_to_func() -> None:
    """Passing alias of borrowed container to function = warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    alias = items
    mutate_list(alias)  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

def test_alias_no_element_borrow() -> None:
    """Alias without element borrows = no warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    alias = items
    alias.append(Point(Int32(3), Int32(4)))  # tpyc: ok
    print(len(items))

def test_direct_still_works() -> None:
    """Direct mutation of borrowed container still warns."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    items.append(Point(Int32(5), Int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))

test_alias_append()
test_alias_subscript_assign()
test_alias_del()
test_alias_pass_to_func()
test_alias_no_element_borrow()
test_direct_still_works()

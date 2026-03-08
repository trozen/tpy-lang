# Warn when borrowed container is passed to non-readonly function parameter
from tpy import Int32, readonly, pure

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def mutate_list(items: list[Point]) -> None:
    items.append(Point(Int32(9), Int32(9)))

@readonly
def read_list(items: list[Point]) -> Int32:
    return Int32(len(items))

@pure
def count_list(items: list[Point]) -> Int32:
    return Int32(len(items))

def safe_read(items: readonly[list[Point]]) -> Int32:
    return Int32(len(items))

def test_pass_borrowed_to_mutating_func() -> None:
    """Borrowed container + non-pure function = warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    mutate_list(items)  # tpyc: warning(/Passing borrowed container 'items'/)
    print(len(items))

def test_pass_borrowed_to_readonly_func() -> None:
    """Borrowed container + @readonly function = no warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    read_list(items)  # tpyc: ok
    print(v.x)

def test_pass_borrowed_to_pure_func() -> None:
    """Borrowed container + @pure function = no warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    count_list(items)  # tpyc: ok
    print(v.x)

def test_pass_borrowed_to_readonly_param() -> None:
    """Borrowed container + readonly[T] param = no warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    safe_read(items)  # tpyc: ok
    print(v.x)

def test_no_borrow_no_warn() -> None:
    """No active borrow = no warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    mutate_list(items)  # tpyc: ok
    print(len(items))

def test_builtin_pure_no_warn() -> None:
    """Builtins like len() are readonly = no warn."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    v = items[Int32(0)]
    print(len(items))  # tpyc: ok
    print(v.x)

test_pass_borrowed_to_mutating_func()
test_pass_borrowed_to_readonly_func()
test_pass_borrowed_to_pure_func()
test_pass_borrowed_to_readonly_param()
test_no_borrow_no_warn()
test_builtin_pure_no_warn()

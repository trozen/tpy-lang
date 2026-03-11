# Tests that Span from list slicing is tracked as a borrow,
# so mutations of the source container trigger warnings.
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def test_span_append_warns() -> None:
    """Span borrows the list; append may reallocate."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4)), Point(Int32(5), Int32(6))]
    span = items[Int32(1):Int32(3)]
    items.append(Point(Int32(9), Int32(9)))  # tpyc: warning(/Mutation of 'items'/)
    print(len(items))

def test_span_subscript_write_warns() -> None:
    """Span borrows the list; subscript write may invalidate."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    span = items[Int32(0):Int32(2)]
    items[Int32(0)] = Point(Int32(9), Int32(9))  # tpyc: warning(/Mutation of 'items'/)
    print(items[Int32(0)].x)

def test_span_del_warns() -> None:
    """Span borrows the list; del invalidates."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    span = items[Int32(0):Int32(2)]
    del items[Int32(0)]  # tpyc: warning(/Mutation of 'items'/)
    print(len(items))

def test_span_no_mutation_no_warn() -> None:
    """Span borrows the list; reading is safe."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    span = items[Int32(0):Int32(2)]
    print(span[Int32(0)].x)
    print(len(items))

def test_no_span_no_warn() -> None:
    """No span active; mutation is fine."""
    items: list[Point] = [Point(Int32(1), Int32(2))]
    items.append(Point(Int32(3), Int32(4)))  # tpyc: ok
    print(len(items))

def test_value_type_span_warns() -> None:
    """Span borrows even for value-type elements (view into container memory)."""
    items: list[Int32] = [Int32(1), Int32(2), Int32(3)]
    span = items[Int32(0):Int32(2)]
    items.append(Int32(9))  # tpyc: warning(/Mutation of 'items'/)
    print(len(items))

test_span_append_warns()
test_span_subscript_write_warns()
test_span_del_warns()
test_span_no_mutation_no_warn()
test_no_span_no_warn()
test_value_type_span_warns()

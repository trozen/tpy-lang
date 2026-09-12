# Tests that Span from list slicing is tracked as a borrow,
# so mutations of the source container trigger warnings.
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def test_span_append_warns() -> None:
    """Span borrows the list; append may reallocate."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4)), Point(int32(5), int32(6))]
    span = items[int32(1):int32(3)]
    items.append(Point(int32(9), int32(9)))  # tpyc: warning(/Mutation of 'items'/)
    print(len(items))

def test_span_subscript_write_ok() -> None:
    """Span borrows the list; subscript write is in-place (no reallocation, no dangling)."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    span = items[int32(0):int32(2)]
    items[int32(0)] = Point(int32(9), int32(9))  # tpyc: ok
    print(items[int32(0)].x)

def test_span_del_warns() -> None:
    """Span borrows the list; del invalidates."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    span = items[int32(0):int32(2)]
    del items[int32(0)]  # tpyc: warning(/Mutation of 'items'/)
    print(len(items))

def test_span_no_mutation_no_warn() -> None:
    """Span borrows the list; reading is safe."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    span = items[int32(0):int32(2)]
    print(span[int32(0)].x)
    print(len(items))

def test_no_span_no_warn() -> None:
    """No span active; mutation is fine."""
    items: list[Point] = [Point(int32(1), int32(2))]
    items.append(Point(int32(3), int32(4)))  # tpyc: ok
    print(len(items))

def test_value_type_span_warns() -> None:
    """Span borrows even for value-type elements (view into container memory)."""
    items: list[int32] = [int32(1), int32(2), int32(3)]
    span = items[int32(0):int32(2)]
    items.append(int32(9))  # tpyc: warning(/Mutation of 'items'/)
    print(len(items))

test_span_append_warns()
test_span_subscript_write_ok()
test_span_del_warns()
test_span_no_mutation_no_warn()
test_no_span_no_warn()
test_value_type_span_warns()

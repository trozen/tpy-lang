# list += copies elements; warn for reference types, allow value types and copy()
from tpy import Int32, copy, Span, Own

class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

def make_nodes() -> Own[list[Node]]:
    return [Node(Int32(1))]

def test_iadd_ref_type_warns() -> None:
    """list[Node] += list[Node] should warn (elements are copied, b still live)."""
    a: list[Node] = []
    b: list[Node] = [Node(Int32(1))]
    a += b  # tpyc: warning(/copies Node elements/)
    print(len(b))  # keep b live so auto-move doesn't suppress the warning

def test_iadd_value_type_no_warn() -> None:
    """list[Int32] += list[Int32] should not warn (value types)."""
    a: list[Int32] = []
    b: list[Int32] = [Int32(1), Int32(2)]
    a += b  # tpyc: ok
    print(len(a))

def test_iadd_copy_no_warn() -> None:
    """Explicit copy() suppresses the warning."""
    a: list[Node] = []
    b: list[Node] = [Node(Int32(1))]
    a += copy(b)  # tpyc: ok
    print(len(b))

def test_iadd_last_use_no_warn() -> None:
    """Auto-move at last use suppresses the warning."""
    a: list[Node] = []
    b: list[Node] = [Node(Int32(1))]
    a += b  # tpyc: ok -- b's last use, auto-moved
    print(len(a))

def test_iadd_rvalue_no_warn() -> None:
    """Rvalue source (function return, list literal) does not warn."""
    a: list[Node] = []
    a += make_nodes()  # tpyc: ok -- rvalue, not a live lvalue
    a += [Node(Int32(2))]  # tpyc: ok -- inline literal
    print(len(a))

def test_iadd_range_no_warn() -> None:
    """list += range (value type elements, no warning)."""
    a: list[Int32] = []
    a += range(Int32(5))  # tpyc: ok
    print(len(a))

def test_iadd_span_warns() -> None:
    """list[Node] += Span[Node] should warn (span borrows, elements still copied)."""
    a: list[Node] = []
    b: list[Node] = [Node(Int32(1)), Node(Int32(2))]
    s: Span[Node] = b
    a += s  # tpyc: warning(/copies Node elements/)
    print(len(b))

def test_iadd_generic_warns[T](a: list[T], b: list[T]) -> None:
    """Generic T: warn 'may copy' since T might not be a value type."""
    a += b  # tpyc: warning(/may copy T elements/)
    print(len(b))

test_iadd_ref_type_warns()
test_iadd_value_type_no_warn()
test_iadd_copy_no_warn()
test_iadd_last_use_no_warn()
test_iadd_rvalue_no_warn()
test_iadd_range_no_warn()
test_iadd_span_warns()
test_iadd_generic_warns([Node(Int32(1))], [Node(Int32(2))])

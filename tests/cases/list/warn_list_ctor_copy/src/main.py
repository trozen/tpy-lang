# list() copies elements from iterables; warn for reference types, allow value types and copy()
from tpy import Int32, copy, Own

class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

def make_nodes() -> Own[list[Node]]:
    return [Node(Int32(1))]

def test_list_ctor_ref_type_warns() -> None:
    """list[Node](list[Node]) should warn (b still live)."""
    b: list[Node] = [Node(Int32(1))]
    a = list(b)  # tpyc: warning(/copies Node elements/)
    print(len(b))

def test_list_ctor_value_type_no_warn() -> None:
    """list[Int32](list[Int32]) should not warn (value types)."""
    b: list[Int32] = [Int32(1), Int32(2)]
    a = list(b)  # tpyc: ok
    print(len(a))

def test_list_ctor_copy_no_warn() -> None:
    """Explicit copy() suppresses the warning."""
    b: list[Node] = [Node(Int32(1))]
    a = list(copy(b))  # tpyc: ok
    print(len(b))

def test_list_ctor_last_use_no_warn() -> None:
    """Auto-move at last use suppresses the warning."""
    b: list[Node] = [Node(Int32(1))]
    a = list(b)  # tpyc: ok -- b's last use
    print(len(a))

def test_list_ctor_rvalue_no_warn() -> None:
    """Rvalue source does not warn."""
    a = list(make_nodes())  # tpyc: ok
    b = list([Node(Int32(2))])  # tpyc: ok
    print(len(a))

def test_list_ctor_generic_warns[T](b: list[T]) -> None:
    """Generic T: warn 'may copy' since T might not be a value type."""
    a = list(b)  # tpyc: warning(/may copy T elements/)
    print(len(b))

test_list_ctor_ref_type_warns()
test_list_ctor_value_type_no_warn()
test_list_ctor_copy_no_warn()
test_list_ctor_last_use_no_warn()
test_list_ctor_rvalue_no_warn()
test_list_ctor_generic_warns([Node(Int32(1))])

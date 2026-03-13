# list.extend() copies elements the same as +=; same warning rules apply
from tpy import Int32, copy, Own

class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

def make_nodes() -> Own[list[Node]]:
    return [Node(Int32(1))]

def test_extend_ref_type_warns() -> None:
    """list[Node].extend(list[Node]) should warn (b still live)."""
    a: list[Node] = []
    b: list[Node] = [Node(Int32(1))]
    a.extend(b)  # tpyc: warning(/copies Node elements/)
    print(len(b))

def test_extend_value_type_no_warn() -> None:
    """list[Int32].extend(list[Int32]) should not warn."""
    a: list[Int32] = []
    b: list[Int32] = [Int32(1)]
    a.extend(b)  # tpyc: ok
    print(len(a))

def test_extend_copy_no_warn() -> None:
    """Explicit copy() suppresses the warning."""
    a: list[Node] = []
    b: list[Node] = [Node(Int32(1))]
    a.extend(copy(b))  # tpyc: ok
    print(len(b))

def test_extend_last_use_no_warn() -> None:
    """Auto-move at last use suppresses the warning."""
    a: list[Node] = []
    b: list[Node] = [Node(Int32(1))]
    a.extend(b)  # tpyc: ok -- b's last use
    print(len(a))

def test_extend_rvalue_no_warn() -> None:
    """Rvalue source does not warn."""
    a: list[Node] = []
    a.extend(make_nodes())  # tpyc: ok
    a.extend([Node(Int32(2))])  # tpyc: ok
    print(len(a))

test_extend_ref_type_warns()
test_extend_value_type_no_warn()
test_extend_copy_no_warn()
test_extend_last_use_no_warn()
test_extend_rvalue_no_warn()

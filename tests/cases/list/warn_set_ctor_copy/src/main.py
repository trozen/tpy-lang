# set() copies elements the same as list(); same warning rules apply
from __future__ import annotations
from tpy import int32, copy, Own

class Node:
    val: int32
    def __init__(self, val: int32) -> None:
        self.val = val
    def __hash__(self) -> int32:
        return self.val
    def __eq__(self, other: Node) -> bool:
        return self.val == other.val

def make_nodes() -> Own[list[Node]]:
    return [Node(int32(1))]

def test_set_ctor_ref_type_warns() -> None:
    """set[Node](list[Node]) should warn (b still live)."""
    b: list[Node] = [Node(int32(1))]
    a = set(b)  # tpyc: warning(/copies Node elements/)
    print(len(b))

def test_set_ctor_value_type_no_warn() -> None:
    """set[int32](list[int32]) should not warn (value types)."""
    b: list[int32] = [int32(1), int32(2)]
    a = set(b)  # tpyc: ok
    print(len(a))

def test_set_ctor_copy_no_warn() -> None:
    """Explicit copy() suppresses the warning."""
    b: list[Node] = [Node(int32(1))]
    a = set(copy(b))  # tpyc: ok
    print(len(b))

def test_set_ctor_last_use_no_warn() -> None:
    """Auto-move at last use suppresses the warning."""
    b: list[Node] = [Node(int32(1))]
    a = set(b)  # tpyc: ok -- b's last use
    print(len(a))

def test_set_ctor_rvalue_no_warn() -> None:
    """Rvalue source does not warn."""
    a = set(make_nodes())  # tpyc: ok
    b = set([Node(int32(2))])  # tpyc: ok
    print(len(a))

test_set_ctor_ref_type_warns()
test_set_ctor_value_type_no_warn()
test_set_ctor_copy_no_warn()
test_set_ctor_last_use_no_warn()
test_set_ctor_rvalue_no_warn()

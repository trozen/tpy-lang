# dict() copies elements from iterables; warn when tuple values are reference types
from tpy import Int32, copy, Own

class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

def make_pairs() -> Own[list[tuple[str, Node]]]:
    return [("a", Node(Int32(1)))]

def test_dict_ctor_ref_value_warns() -> None:
    """dict from list[tuple[str, Node]] should warn (Node is a reference type)."""
    pairs: list[tuple[str, Node]] = [("a", Node(Int32(1)))]
    d = dict(pairs)  # tpyc: warning(/copies tuple\[str, Node\] elements/)
    print(len(pairs))

def test_dict_ctor_value_types_no_warn() -> None:
    """dict from list[tuple[str, Int32]] should not warn (all value types)."""
    pairs: list[tuple[str, Int32]] = [("a", Int32(1))]
    d = dict(pairs)  # tpyc: ok
    print(len(d))

def test_dict_ctor_copy_no_warn() -> None:
    """Explicit copy() suppresses the warning."""
    pairs: list[tuple[str, Node]] = [("a", Node(Int32(1)))]
    d = dict(copy(pairs))  # tpyc: ok
    print(len(pairs))

def test_dict_ctor_last_use_no_warn() -> None:
    """Auto-move at last use suppresses the warning."""
    pairs: list[tuple[str, Node]] = [("a", Node(Int32(1)))]
    d = dict(pairs)  # tpyc: ok -- pairs last use
    print(len(d))

def test_dict_ctor_rvalue_no_warn() -> None:
    """Rvalue source does not warn."""
    d = dict(make_pairs())  # tpyc: ok
    print(len(d))

def test_dict_ctor_generic_warns[K, V](pairs: list[tuple[K, V]]) -> None:
    """Generic K, V: warn 'may copy' since types may not be value types."""
    d = dict(pairs)  # tpyc: warning(/may copy tuple\[K, V\] elements/)
    print(len(pairs))

def test_dict_ctor_nested_tuple_warns() -> None:
    """Node nested inside a tuple value -- recursive check still fires."""
    pairs: list[tuple[str, tuple[str, Node]]] = []
    d = dict(pairs)  # tpyc: warning(/copies tuple\[str, tuple\[str, Node\]\] elements/)
    print(len(pairs))

def test_dict_ctor_list_value_warns() -> None:
    """list[Node] as tuple value -- list is a reference type."""
    pairs: list[tuple[str, list[Node]]] = []
    d = dict(pairs)  # tpyc: warning(/copies tuple\[str, list\[Node\]\] elements/)
    print(len(pairs))

def test_dict_ctor_nested_value_no_warn() -> None:
    """All-value-type nested tuple -- no warning."""
    pairs: list[tuple[str, tuple[str, Int32]]] = []
    d = dict(pairs)  # tpyc: ok
    print(len(pairs))

def test_dict_ctor_partial_generic_warns[V](pairs: list[tuple[str, V]]) -> None:
    """str is concrete value type, V unknown -- should 'may copy'."""
    d = dict(pairs)  # tpyc: warning(/may copy tuple\[str, V\] elements/)
    print(len(pairs))

def test_dict_ctor_nested_generic_warns[K, V](pairs: list[tuple[K, tuple[str, V]]]) -> None:
    """V nested inside inner tuple -- recursive check still fires."""
    d = dict(pairs)  # tpyc: warning(/may copy tuple\[K, tuple\[str, V\]\] elements/)
    print(len(pairs))

test_dict_ctor_ref_value_warns()
test_dict_ctor_value_types_no_warn()
test_dict_ctor_copy_no_warn()
test_dict_ctor_last_use_no_warn()
test_dict_ctor_rvalue_no_warn()
test_dict_ctor_generic_warns([("a", Node(Int32(1)))])
test_dict_ctor_nested_tuple_warns()
test_dict_ctor_list_value_warns()
test_dict_ctor_nested_value_no_warn()
test_dict_ctor_partial_generic_warns([("a", Node(Int32(1)))])
test_dict_ctor_nested_generic_warns([("a", ("b", Node(Int32(1))))])

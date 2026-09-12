# dict.update() and |= copy values from the source dict; warn when V is a reference type
from tpy import int32, copy, Own


class Node:
    val: int32

    def __init__(self, val: int32) -> None:
        self.val = val


def make_dict() -> Own[dict[str, Node]]:
    return {"a": Node(int32(1))}


def test_warn_update_not_last_use() -> None:
    """b still live after update -- values are copied (CPython would share refs)."""
    a: dict[str, Node] = {}
    b: dict[str, Node] = {"a": Node(int32(1))}
    a.update(b)  # tpyc: warning(/copies Node elements/)
    print(len(b))


def test_no_warn_update_last_use() -> None:
    """Auto-move at last use suppresses the warning."""
    a: dict[str, Node] = {}
    b: dict[str, Node] = {"a": Node(int32(1))}
    a.update(b)  # tpyc: ok -- b's last use
    print(len(a))


def test_no_warn_update_explicit_copy() -> None:
    """Explicit copy() suppresses the warning."""
    a: dict[str, Node] = {}
    b: dict[str, Node] = {"a": Node(int32(1))}
    a.update(copy(b))  # tpyc: ok
    print(len(b))


def test_no_warn_update_rvalue() -> None:
    """Rvalue source does not warn."""
    a: dict[str, Node] = {}
    a.update(make_dict())  # tpyc: ok
    a.update({"b": Node(int32(2))})  # tpyc: ok
    print(len(a))


def test_no_warn_update_value_types() -> None:
    """No warning for all-value-type dicts."""
    a: dict[str, int32] = {}
    b: dict[str, int32] = {"a": int32(1)}
    a.update(b)  # tpyc: ok
    print(len(b))


def test_warn_ior_not_last_use() -> None:
    """|= warns when b is still live."""
    a: dict[str, Node] = {}
    b: dict[str, Node] = {"a": Node(int32(1))}
    a |= b  # tpyc: warning(/copies Node elements/)
    print(len(b))


def test_no_warn_ior_last_use() -> None:
    """|= suppressed on last use."""
    a: dict[str, Node] = {}
    b: dict[str, Node] = {"a": Node(int32(1))}
    a |= b  # tpyc: ok -- b's last use
    print(len(a))


def test_warn_update_generic[V](a: dict[str, V], b: dict[str, V]) -> None:
    """Generic V: warn 'may copy' since type may not be value type."""
    a.update(b)  # tpyc: warning(/may copy V elements/)
    print(len(b))


test_warn_update_not_last_use()
test_no_warn_update_last_use()
test_no_warn_update_explicit_copy()
test_no_warn_update_rvalue()
test_no_warn_update_value_types()
test_warn_ior_not_last_use()
test_no_warn_ior_last_use()
g_a: dict[str, Node] = {"x": Node(int32(0))}
g_b: dict[str, Node] = {"a": Node(int32(1))}
test_warn_update_generic(g_a, g_b)

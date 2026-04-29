# Storing a reference type (record/list/dict/set) into Any silently
# copies (Principle #1: Any owns its contents). Warn at the storage
# site and suggest copy() to acknowledge the copy explicitly.
# Suppressed for rvalue sources, last-use auto-move, value types,
# str (view->std::string collapse), and bytes.

from typing import Any
from tpy import copy, Int32


class Node:
    def __init__(self, n: Int32) -> None:
        self.n = n


def warn_record_not_last_use() -> None:
    n = Node(Int32(1))
    a: Any = n  # tpyc: warning(/copies Node into Any/)
    print(n.n)


def no_warn_record_last_use() -> None:
    n = Node(Int32(2))
    a: Any = n  # tpyc: ok -- n's last use, auto-moved
    if isinstance(a, Node):
        print(a.n)


def no_warn_record_explicit_copy() -> None:
    n = Node(Int32(3))
    a: Any = copy(n)  # tpyc: ok -- explicit copy
    print(n.n)


def no_warn_record_rvalue() -> None:
    a: Any = Node(Int32(4))  # tpyc: ok -- rvalue, no other handle
    if isinstance(a, Node):
        print(a.n)


def warn_list_not_last_use() -> None:
    xs: list[Int32] = [Int32(1), Int32(2)]
    a: Any = xs  # tpyc: warning(/copies list\[Int32\] into Any/)
    print(len(xs))


def warn_dict_not_last_use() -> None:
    d: dict[str, Int32] = {"k": Int32(1)}
    a: Any = d  # tpyc: warning(/copies dict\[str, Int32\] into Any/)
    print(len(d))


def warn_set_not_last_use() -> None:
    s: set[Int32] = {Int32(1), Int32(2)}
    a: Any = s  # tpyc: warning(/copies set\[Int32\] into Any/)
    print(len(s))


def no_warn_str_not_last_use() -> None:
    s = "hello"
    a: Any = s  # tpyc: ok -- str collapses to std::string at INTO_ANY
    print(s)


def no_warn_int_not_last_use() -> None:
    x = Int32(42)
    a: Any = x  # tpyc: ok -- value type
    print(x)


def takes_any(a: Any) -> None:
    if isinstance(a, Node):
        print(a.n)


def warn_arg_coerce_not_last_use() -> None:
    n = Node(Int32(5))
    takes_any(n)  # tpyc: warning(/copies Node into Any/)
    print(n.n)


def returns_any(n: Node) -> Any:
    return n  # tpyc: warning(/copies Node into Any/)


def exercise_returns_any() -> None:
    # Warning fires inside returns_any (the `return n` line); the call
    # site itself is Any -> Any, no INTO_ANY, no extra warning here.
    n = Node(Int32(6))
    a = returns_any(n)  # tpyc: ok -- result is already Any
    print(n.n)
    if isinstance(a, Node):
        print(a.n)


def warn_list_literal_element() -> None:
    n = Node(Int32(7))
    xs: list[Any] = [n]  # tpyc: warning(/copies Node into Any/)
    print(n.n)
    print(len(xs))


def warn_dict_literal_value() -> None:
    n = Node(Int32(8))
    d: dict[str, Any] = {"k": n}  # tpyc: warning(/copies Node into Any/)
    print(n.n)
    print(len(d))


def warn_subscript_assign_dict_any() -> None:
    n = Node(Int32(9))
    d: dict[str, Any] = {}
    d["k"] = n  # tpyc: warning(/copies Node into Any/)
    print(n.n)


class Holder:
    payload: Any

    def __init__(self) -> None:
        self.payload = None


def warn_field_assign_any() -> None:
    n = Node(Int32(10))
    h = Holder()
    h.payload = n  # tpyc: warning(/copies Node into Any/)
    print(n.n)


def main() -> None:
    warn_record_not_last_use()
    no_warn_record_last_use()
    no_warn_record_explicit_copy()
    no_warn_record_rvalue()
    warn_list_not_last_use()
    warn_dict_not_last_use()
    warn_set_not_last_use()
    no_warn_str_not_last_use()
    no_warn_int_not_last_use()
    warn_arg_coerce_not_last_use()
    exercise_returns_any()
    warn_list_literal_element()
    warn_dict_literal_value()
    warn_subscript_assign_dict_any()
    warn_field_assign_any()


main()

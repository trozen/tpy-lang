# dict[str, Rc[T]] literal -- builds via make_ordered_map so move-only
# values (@nocopy Rc) survive construction. Mutation through one alias
# is visible through other clones of the same allocation.
from tpy import Int32
from tplib import Rc


class Node:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


def main() -> None:
    a = Rc.new(Node(1))
    b = Rc.new(Node(2))

    a_alias = a.clone()

    items: dict[str, Rc[Node]] = {"a": a.clone(), "b": b.clone()}

    a_alias.get().value = 99
    print(items["a"].get().value)  # 99 -- shared

    items["b"].get().value = 42
    print(b.get().value)  # 42 -- shared

    # Single-entry literal -- smallest make_ordered_map invocation.
    single: dict[str, Rc[Node]] = {"only": a.clone()}
    print(single["only"].get().value)  # 99 -- still shared with a

    # Empty literal + per-key assign -- exercises ordered_map default ctor
    # then __setitem__ -> insert_or_assign(V&&) move path.
    grown: dict[str, Rc[Node]] = {}
    grown["x"] = a.clone()
    grown["y"] = b.clone()
    print(grown["x"].get().value)  # 99
    print(grown["y"].get().value)  # 42

    # Overwrite existing key -- exercises insert_or_assign's update branch
    # (it != end) with a move-only value.
    grown["x"] = b.clone()
    print(grown["x"].get().value)  # 42


main()

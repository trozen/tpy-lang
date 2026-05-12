# list[Rc[T]] -- storing Rc clones in a container. Mutation through
# one alias is visible through other clones of the same allocation.
from tpy import Int32
from tplib import Rc, make_rc


class Node:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


def main() -> None:
    a = make_rc(Node(Int32(1)))
    b = make_rc(Node(Int32(2)))
    c = make_rc(Node(Int32(3)))

    a_alias = a.clone()

    items: list[Rc[Node]] = [a.clone(), b.clone(), c.clone()]

    # Mutate via a_alias, then read through the list element.
    a_alias.get().value = Int32(99)
    print(items[0].get().value)  # 99 -- shared

    # Mutate via list, read via b.
    items[1].get().value = Int32(42)
    print(b.get().value)  # 42 -- shared


main()

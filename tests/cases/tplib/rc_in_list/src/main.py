# list[Rc[T]] -- storing Rc clones in a container. Mutation through
# one alias is visible through other clones of the same allocation.
from tpy import int32
from tplib import Rc


class Node:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value


def main() -> None:
    a = Rc.new(Node(int32(1)))
    b = Rc.new(Node(int32(2)))
    c = Rc.new(Node(int32(3)))

    a_alias = a.clone()

    items: list[Rc[Node]] = [a.clone(), b.clone(), c.clone()]

    # Mutate via a_alias, then read through the list element.
    a_alias.get().value = int32(99)
    print(items[0].get().value)  # 99 -- shared

    # Mutate via list, read via b.
    items[1].get().value = int32(42)
    print(b.get().value)  # 42 -- shared


main()

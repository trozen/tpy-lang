# tuple[Rc[T], ...] -- storing Rc handles in tuples. Locks down the
# borrow/storage form interaction for shared-ownership values in tuple
# slots. Mutation through one tuple slot is visible through the other
# clones; tuple lifetime is shorter than the underlying allocation
# (refcount keeps it alive).
from tpy import Int32
from tplib import Rc


class Node:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


def both(t: tuple[Rc[Node], Rc[Node]]) -> Int32:
    # Borrow form: tuple of Rc[T] borrows.
    return t[0].get().value + t[1].get().value


def main() -> None:
    a = Rc.new(Node(Int32(10)))
    b = Rc.new(Node(Int32(20)))

    pair: tuple[Rc[Node], Rc[Node]] = (a.clone(), b.clone())

    print(both(pair))  # 30

    # Mutate through one slot; the original handle sees it.
    pair[0].get().value = Int32(100)
    print(a.get().value)  # 100 -- shared

    # Mutate through the original; the tuple slot sees it.
    b.get().value = Int32(200)
    print(pair[1].get().value)  # 200 -- shared


main()

# List comprehension with tuple-unpack where one slot is @nocopy and the
# body calls a non-readonly method on it. Verifies the unpack tmp + non-value
# slot binding both drop const when sema detects mutation.
from tpy import int32, Own
from tplib import Rc


class Node:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v


def make_pair(i: int32, v: int32) -> Own[tuple[int32, Rc[Node]]]:
    return (i, Rc.new(Node(v)))


def main() -> None:
    pairs: list[tuple[int32, Rc[Node]]] = []
    pairs.append(make_pair(1, 10))
    pairs.append(make_pair(2, 20))
    clones: list[Rc[Node]] = [r.clone() for (_, r) in pairs]
    for c in clones:
        print(c.get().value)


main()

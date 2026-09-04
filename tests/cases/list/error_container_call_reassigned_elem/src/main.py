# The adjacent reassigned-from-a-call shape that keeps rejecting: a
# RECORD-element container, whose element family the storage-call return set
# does not admit -- the rebind slot only carries the scalar-read families.
from tpy import Own


class Node:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def make(n: int) -> Own[list[Node]]:
    out: list[Node] = []
    out.append(Node(n))
    return out


def main():
    r = make(3)  # tpyc: error(/decl.slot_type/)
    print(len(r))
    r = make(7)
    print(len(r))


main()

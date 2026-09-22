# A local bound from an OWNING call and later rebound from another owns each
# value in turn, for a RECORD-element container exactly as for a scalar one:
# the rebind slot admits the element family through the shared record-or-
# container gate. Each binding is mutated and read back, so a slot that
# copied or aliased the wrong value would show.
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
    r = make(3)  # tpyc: ok
    r.append(Node(4))
    print(len(r), r[0].n, r[1].n)
    r = make(7)
    r[0].n += 1
    print(len(r), r[0].n)


main()

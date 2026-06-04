# A resumable generator method iterating self.<field> must BORROW the field,
# not copy it into the frame: element mutations through the loop var persist
# after iteration (CPython aliasing; was silently lost on a frame copy).
from typing import Iterator
from tpy import Int32


class Node:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val


class Holder:
    nodes: list[Node]

    def __init__(self) -> None:
        self.nodes = [Node(1), Node(2)]

    def bump(self) -> Iterator[Int32]:
        yield 0
        for n in self.nodes:  # tpyc: ok
            n.val += 10
            yield n.val


def main() -> None:
    h = Holder()
    for v in h.bump():
        print(v)
    print(h.nodes[0].val, h.nodes[1].val)


main()

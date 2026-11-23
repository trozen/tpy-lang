# A body-less native method declaring no `result=` whose result points
# (a reference type) is assumed to borrow its receiver AND every operand
# that can lend: a temporary argument is warned, a named one is lent
# (a write through the result reaches it).
from typing import Iterator
from tpy import int32
from tpy.extern import native
from fixtures import Node
from part_twin import main as twin_main
from part_d3 import main as d3_main
from part_frames import main as frames_main
from part_mem import main as mem_main
from part_generic import main as generic_main
from part_lend_reach import main as reach_main


@native
class Picker:
    def __init__(self) -> None: ...

    # Not `@readonly`: that would make `other` readonly, as on the TPy twin,
    # and its C++ takes it by mutable reference.
    @native("pick")
    def pick(self, other: Node) -> Node: ...


def free() -> None:
    p = Picker()
    n = Node(1)
    # free function: a named operand lends -- the write reaches `n`
    r = p.pick(n)  # tpyc: ok
    r.v = 5
    print("free:", n.v)
    # free function: a temporary operand may be what the result points to
    # its own picker: `pick` is not `@readonly`, so a second call on `p`
    # while `r` borrows it would warn the mutation of `p` instead
    q = Picker()
    t = q.pick(Node(2))  # tpyc: warning(/Result borrows from temporary argument 'other'/)
    print("free: bound")


class User:
    p: Picker
    n: Node

    def __init__(self) -> None:
        self.p = Picker()
        self.n = Node(3)

    def bump(self) -> int32:
        # method: a field operand lends
        r = self.p.pick(self.n)  # tpyc: ok
        r.v += 1
        return self.n.v


def gen(p: Picker, n: Node) -> Iterator[int32]:
    # generator
    r = p.pick(n)  # tpyc: ok
    r.v = 7
    yield n.v


def main() -> None:
    free()
    print("method:", User().bump())
    p = Picker()
    n = Node(0)
    for v in gen(p, n):
        print("generator:", v)
    twin_main()
    d3_main()
    frames_main()
    mem_main()
    generic_main()
    reach_main()


main()

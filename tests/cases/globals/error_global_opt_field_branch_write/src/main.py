# A pointer-slot global written from an Optional-record FIELD inside a branch.
# Only rvalue sources (and a pointer-name copy) lower in a branch, so this
# lvalue lift keeps rejecting -- the top-level flavor is the one that lowers.
from tpy import int32


class Inner:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Outer:
    inner: Inner | None

    def __init__(self) -> None:
        self.inner = Inner(7)


o: Outer = Outer()
flag = True
g: Inner | None = None
if flag:
    g = o.inner  # tpyc: error(/not yet supported.*global_slot_branch/)
    if g is not None:
        print(g.n)

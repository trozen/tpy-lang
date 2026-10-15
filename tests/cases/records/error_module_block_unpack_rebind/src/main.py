# A module-level reference name first bound inside a block by a tuple unpack
# and rebound later is refused, like its declaration twin: a module global's
# rebind slot needs storage of the module's own
# (BUGS.md#module-block-unpack-rebind-rejects).
from tpy import int32, Own


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk(i: int32) -> tuple[Own[P], int32]:
    return (P(i), i)


c = True
if c:
    p, k = mk(1)  # tpyc: error(/top_level\.slot_alloc/)
    q = p
    p = P(2)
    q.n += 10
    print("if", q.n, p.n, k)

# A module-level `if` that binds a borrow tuple from an owning tuple call in
# one arm needs a shared rebind slot, which has no module-scope spelling: the
# hoist refuses it rather than leave the global pointing into a dead frame.
from tpy import int32, Own


class B:
    def __init__(self, v: int32) -> None:
        self.v = v


def mkt(v: int32) -> tuple[int32, Own[B]]:
    return (v, B(v))


src = B(100)
k = 1
# The hoisted `t` aliases `src` in one arm and owns mkt's B in the other.
if k > 0:  # tpyc: error(/top_level.slot_alloc/)
    t = (1, src)
else:
    t = mkt(5)
t[1].v += 1
print("mod", t[0], t[1].v, src.v)

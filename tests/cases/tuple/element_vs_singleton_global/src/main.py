# A tuple element at a module GLOBAL behaves as the same type would as a
# singleton there. The scalar reference global is a pointer slot (`Box* G`)
# and ALIASES the object it was given; a tuple of references is the tuple of
# those slots (`std::tuple<Box*, Box*>`) and aliases element-wise, so a write
# through `pair[0]` reaches V exactly as one through `singleton` does. A tuple
# that OWNS a fresh element (`(1, Box(5))`) is storage, as the local twin is.
# The MIXED form (an owned element beside a borrowed one, from a call) still
# copies its borrowed element; that open half is pinned in its own case,
# `tuple/mixed_tuple_global_copies` (BUGS.md#global-tuple-ref-storage-form).
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


V = Box(2)
W = Box(3)
singleton: Box = V  # tpyc: warning(/will not keep the object/)
# all-borrow tuple global: the tuple of the pointer slots, aliasing V.
pair: tuple[Box, Box] = (V, V)  # tpyc: ok
# a fresh element makes the global OWN it: storage, nothing to alias.
owned: tuple[int32, Box] = (1, Box(5))  # tpyc: ok
# top-level rebind of a borrow tuple global re-points the slots, as `singleton = W` would.
pair2: tuple[Box, int32] = (V, 1)  # tpyc: ok
pair2 = (W, 2)  # tpyc: ok


def main() -> None:
    singleton.n = 42
    print("scalar", V.n)

    V.n = 2
    pair[0].n = 43
    print("tuple", V.n)

    owned[1].n = 9
    print("owned", owned[1].n)

    pair2[0].n = 7
    print("rebound", W.n, V.n)


main()

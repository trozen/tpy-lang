# A tuple element at a module GLOBAL behaves as the same type would as a
# singleton there. The scalar reference global is a pointer slot (`Box* G`)
# and ALIASES the object it was given; a tuple of references is the tuple of
# those slots (`std::tuple<Box*, Box*>`) and aliases element-wise, so a write
# through `pair[0]` reaches V exactly as one through `singleton` does. A tuple
# with a fresh element (`(1, Box(5))`), or the MIXED one from a call (an owned
# element beside a borrowed one), parks its value in a static of its own
# layout, as the scalar parks its `static Box __global_slot_N`, and the global
# is the same tuple of pointer slots aimed at it; positions in
# `tuple/mixed_tuple_global`.
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


V = Box(2)
W = Box(3)
singleton: Box = V  # tpyc: warning(/will not keep the object/)
# all-borrow tuple global: the tuple of the pointer slots, aliasing V.
pair: tuple[Box, Box] = (V, V)  # tpyc: ok
# a fresh element parks in a static; the global's slot aims at it.
owned: tuple[int32, Box] = (1, Box(5))  # tpyc: ok
# top-level rebind of a borrow tuple global re-points the slots, as `singleton = W` would.
pair2: tuple[Box, int32] = (V, 1)  # tpyc: ok
pair2 = (W, 2)  # tpyc: ok
# mixed: the borrowed element aliases V like the local twin's.
mixed: tuple[Box, Box] = make_mixed(V)  # tpyc: ok


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

    # the mixed local and the mixed global both write through to V.
    p = make_mixed(V)
    p[1].n = 43
    print("local", V.n)
    V.n = 2
    mixed[1].n = 44  # tpyc: ok
    print("mixed_write", V.n)


main()

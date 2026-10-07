# A tuple stored into a container element behaves as the same tuple stored
# into a field: the store OWNS, so a borrowed element is copied (warned, as
# the scalar's copy is) and an owned element at its last use moves --
# whatever spelled the source: a literal, a local, a call, an element read.
from tpy import int32, Own


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def make_pair(b: Box) -> tuple[Box, int32]:
    return (b, 2)


# free function: a literal at a dict value slot (the `append` twin compiled
# before; the copy is the field store's)
def setitem_literal(d: dict[str, tuple[Box, int32]], b: Box) -> None:
    d["a"] = (b, 1)  # tpyc: warning(/copies Box into container \(tuple element 0\)/)


# free function: a MIXED call-bound local appended at its last use moves its
# owned element and copies the borrowed one
def append_mixed_last_use(xs: list[tuple[Box, Box]], b: Box) -> None:
    t = make_mixed(b)
    xs.append(t)  # tpyc: warning(/copies Box into owned storage \(tuple element 1\)/)


# free function: the same local still live after the append copies both
def append_mixed_live(xs: list[tuple[Box, Box]], b: Box) -> int32:
    t = make_mixed(b)
    xs.append(t)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
    return t[0].n


# free function: a nested literal appended (the dict twin compiled before)
def append_nested_literal(xs: list[tuple[int32, tuple[int32, Box]]], c: Box) -> None:
    xs.append((1, (2, c)))  # tpyc: warning(/copies Box into owned storage \(tuple element 1.1\)/)


# free function: a nested value tuple copied between containers by an
# element read
def copy_nested_between_dicts(src: dict[int32, tuple[int32, tuple[int32, int32]]],
                              d: dict[int32, tuple[int32, tuple[int32, int32]]]) -> None:
    d[1] = src[5]  # tpyc: ok


# free function: a borrow-returning call stored at a dict value and appended
def store_call_result(d: dict[int32, tuple[Box, int32]],
                      xs: list[tuple[Box, int32]], b: Box) -> None:
    d[0] = make_pair(b)  # tpyc: warning(/copies Box into container \(tuple element 0\)/)
    xs.append(make_pair(b))  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)


# free function: a container element read stored into another container
# copies the element's Box (the containers do not share it)
def store_element_read(d: dict[int32, tuple[Box, int32]],
                       xs: list[tuple[Box, int32]]) -> None:
    xs.append(d[0])  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    d[7] = xs[0]  # tpyc: warning(/copies Box into container \(tuple element 0\)/)


# free function: a nested tuple of plain values appended by name copies
# like any value (no reference inside, nothing to declare)
def append_nested_scalar(xs: list[tuple[int32, tuple[int32, int32]]],
                         t: tuple[int32, tuple[int32, int32]]) -> None:
    xs.append(t)  # tpyc: ok


# method: a user signature's whole `Own[tuple]` parameter takes a mixed
# call's result like the insert slot does
class Taker:
    def take(self, t: Own[tuple[Box, Box]]) -> int32:
        return t[0].n + t[1].n


def user_slot_mixed_call(s: Taker, b: Box) -> int32:
    return s.take(make_mixed(b))  # tpyc: warning(/tuple element 1\)/)


# free function: an Own parameter stored as a tuple member moves in and is
# consumed (no copy warning, no "never consumed" warning), at a dict value
# and at a field alike
class Slot:
    t: tuple[Box, int32]

    def __init__(self) -> None:
        self.t = (Box(0), 0)


def own_param_member(d: dict[int32, tuple[Box, int32]], q: Own[Box]) -> None:
    d[0] = (q, 1)  # tpyc: ok


def own_param_member_field(s: Slot, q: Own[Box]) -> None:
    s.t = (q, 2)  # tpyc: ok


# method: the same stores off `self`
class Keeper:
    xs: list[tuple[Box, Box]]
    d: dict[str, tuple[Box, int32]]

    def __init__(self) -> None:
        self.xs = []
        self.d = {}

    def keep(self, b: Box) -> None:
        t = make_mixed(b)
        self.xs.append(t)  # tpyc: warning(/tuple element 1\)/)
        self.d["k"] = (b, 3)  # tpyc: warning(/tuple element 0\)/)


def main() -> None:
    # Every store below copies its borrowed element (warned above), so the
    # prints read the STORED tuples only, which CPython and TPy agree on;
    # each section gets its own Box so no alias crosses sections.
    d: dict[str, tuple[Box, int32]] = {}
    setitem_literal(d, Box(0))
    ta = d["a"]
    ta[0].n += 5
    print("setitem_literal", ta[0].n, ta[1])
    xs: list[tuple[Box, Box]] = []
    append_mixed_last_use(xs, Box(0))
    live = append_mixed_live(xs, Box(0))
    x0 = xs[0]
    x0[1].n += 7
    print("append_mixed_last_use", x0[0].n, x0[1].n)
    x1 = xs[1]
    print("append_mixed_live", live, x1[0].n, x1[1].n)
    ys: list[tuple[int32, tuple[int32, Box]]] = []
    append_nested_literal(ys, Box(0))
    # (the nested element does not read back yet:
    # BUGS.md#nested-storage-tuple-element-read)
    print("append_nested_literal", len(ys))
    src = {5: (1, (2, 3))}
    nd: dict[int32, tuple[int32, tuple[int32, int32]]] = {}
    copy_nested_between_dicts(src, nd)
    print("copy_nested_between_dicts", nd[1])
    pd: dict[int32, tuple[Box, int32]] = {}
    ps: list[tuple[Box, int32]] = []
    store_call_result(pd, ps, Box(0))
    t0 = pd[0]
    t0[0].n += 11
    print("store_call_result", t0[0].n, t0[1], len(ps))
    store_element_read(pd, ps)
    p1 = ps[1]
    p1[0].n += 13
    t7 = pd[7]
    print("store_element_read", p1[0].n, t7[1], len(ps))
    ns: list[tuple[int32, tuple[int32, int32]]] = []
    append_nested_scalar(ns, (1, (2, 3)))
    print("append_nested_scalar", ns[0])
    print("user_slot_mixed_call", user_slot_mixed_call(Taker(), Box(4)))
    od: dict[int32, tuple[Box, int32]] = {}
    own_param_member(od, Box(21))
    o0 = od[0]
    sl = Slot()
    own_param_member_field(sl, Box(22))
    print("own_param_member", o0[0].n, o0[1], sl.t[0].n, sl.t[1])
    k = Keeper()
    kb = Box(0)
    k.keep(kb)
    kk = k.d["k"]
    kk[0].n += 15
    kx = k.xs[0]
    print("method", kx[0].n, kk[0].n, kk[1])


main()

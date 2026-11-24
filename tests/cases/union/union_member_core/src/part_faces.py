# A union result's declared copy is spelled under its member, as the twin's
# copy is: a list local returned past a `finally`, a branch-mixed local, a
# closure-read local and a walrus bound to a borrowed return each copy into
# the member (the `cpp_type` faces of BUGS.md#union-landing-unread-positions).
# Those copies are declared and warned, so no write after them is compared
# with CPython; a borrowed union result is the caller's object itself.
from tpy import Own, int32


class RBase:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]


# return past a `finally`: the list the body hands back is the one the
# `finally` appends to
def fin_union() -> Own[list[int32] | int32]:
    xs = [1]
    try:
        return xs
    finally:
        xs.append(2)


def fin_twin() -> Own[list[int32]]:
    xs = [1]
    try:
        return xs
    finally:
        xs.append(2)


# a local bound to the caller's object on one branch and a fresh one on
# the other
def mixed_union(p: RBase, f: bool) -> Own[RBase | int32]:
    if f:
        r = p
    else:
        r = RBase()
    return r


def mixed_twin(p: RBase, f: bool) -> Own[RBase]:
    if f:
        r = p
    else:
        r = RBase()
    return r


# a local a nested def still reads
def closure_union() -> Own[RBase | int32]:
    r = RBase()

    def peek() -> int:
        return len(r.items)
    print("closure-peek", peek())
    return r


def closure_twin() -> Own[RBase]:
    r = RBase()

    def peek() -> int:
        return len(r.items)
    print("closure-peek", peek())
    return r


class Holder:
    r: RBase

    def __init__(self) -> None:
        self.r = RBase()

    def get(self) -> RBase:
        return self.r


# a walrus bound to a borrowed return: the holder's object
def walrus_union(h: Holder) -> Own[RBase | int32]:
    if len((r := h.get()).items) > 0:
        return r
    return 0


def walrus_twin(h: Holder) -> Own[RBase]:
    r = h.get()
    return r


class RSub(RBase):
    pass


# a borrowed union result: the caller's object, whose later write shows
def pick_union(x: RSub, ys: list[int32], k: int32) -> RBase | list[int32]:
    if k > 0:
        # the base member lends only the base part, as the twin does
        return x  # tpyc: warning(/upcast narrows/)
    return ys


def pick_twin(x: RSub) -> RBase:
    # a base result lends only the base part
    return x  # tpyc: warning(/upcast narrows/)


def size(u: RBase | int32) -> int32:
    if isinstance(u, RBase):
        return len(u.items)
    return -1


def main() -> None:
    # the list the `finally` appends to is the one handed back
    f = fin_union()
    if isinstance(f, list):
        print("fin", len(f), len(fin_twin()))
    p = RBase()
    m = mixed_union(p, True)
    t = mixed_twin(p, True)
    print("mixed", size(m), len(t.items))
    cu = closure_union()
    c = closure_twin()
    print("closure", size(cu), len(c.items))
    h = Holder()
    w = walrus_union(h)
    wt = walrus_twin(h)
    print("walrus", size(w), len(wt.items))
    s = RSub()
    ys = [1]
    pu = pick_union(s, ys, 1)
    pt = pick_twin(s)
    s.items.append(4)
    if isinstance(pu, RBase):
        print("pick", len(pu.items), len(pt.items))

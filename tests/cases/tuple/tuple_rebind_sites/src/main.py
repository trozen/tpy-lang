# Tuple bindings are rebind sites: a rebind of a tuple that holds an object of
# its own is decided as the scalar's is -- in place when nothing can observe
# the old tuple's objects, warned where a module loop's one slot cannot keep
# what an earlier pass bound -- while a tuple of borrowed names only re-points.
# Each section mutates through a name after the rebind and prints what the
# other names see, so a silent copy or clobber would show.
from typing import Iterator

from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def pick(a: Box, b: Box) -> tuple[Box, Box]:
    return (b, a)


def identity(p: tuple[Box, Box]) -> tuple[Box, Box]:
    return p


V = Box(2)
W = Box(3)

# module loop: the site's hoisted slot is assigned on every pass, and 'saved'
# still names the first pass's tuple, so the rebind warns as the scalar's does.
M = make_mixed(1, V)
saved = M
for i in range(3):
    M = make_mixed(10 * i, V)  # tpyc: warning(/'saved' will not keep the object it was given -- 'M' is rebound here and both names share its storage; bind the new value to a name of its own/)
    M[1].n += 1
    if i == 0:
        saved = M
        saved[0].n += 100
    # the borrowed element is the lender itself, whichever pass bound 'saved'.
    print("module-loop", saved[1].n, M[0].n, V.n)

# module loop, the alias taken through a borrowing call: a module function's
# borrow facts are not known yet, and unknown means aliased, so the call's
# result holds the tuple and the rebind warns as above.
C = make_mixed(1, V)
held = identity(C)
for i in range(2):
    C = make_mixed(30 + i, V)  # tpyc: warning(/'held' will not keep the object it was given -- 'C' is rebound here/)
    if i == 0:
        held = identity(C)
    # the borrowed element is the lender itself, whichever pass bound `held`.
    print("module-loop-call", held[1].n, C[0].n, V.n)

# the scalar global under a module function's borrow: the same rule gives the
# rebind storage of its own, so the result keeps the old object.
S = Box(40)
R = identity((S, S))
S = Box(50)  # tpyc: ok
R[0].n += 1
print("module-lender", R[0].n, S.n)

# module rebind after an unpack: each write parks a static of its own, so what
# the unpack took from the old tuple keeps it.
P = make_mixed(5, W)
a0, a1 = P
P = make_mixed(6, V)  # tpyc: ok
a0.n += 1
P[0].n += 10
W.n += 1
print("module-unpack", a0.n, P[0].n, a1.n)

# module literal with a fresh element, rebound in a branch and in a loop with
# nothing else naming the old tuple.
L = (Box(7), W)
if V.n > 0:
    L = (Box(8), W)  # tpyc: ok
for j in range(2):
    L = (Box(20 + j), V)  # tpyc: ok
    L[1].n += 1
print("module-literal", L[0].n, L[1].n, V.n)


# function body: a mixed local rebound with nothing else naming the old tuple
# writes in place, in a branch and in a loop.
def mixed_in_place(c: bool) -> None:
    p = make_mixed(1, V)
    if c:
        p = make_mixed(2, W)  # tpyc: ok
    p[1].n += 1
    for k in range(2):
        p = make_mixed(3 + k, V)  # tpyc: ok
        p[0].n += 10
    print("mixed-in-place", p[0].n, p[1].n, V.n, W.n)


# borrowed names: the tuple holds nothing of its own, so the rebind re-points
# and the element alias keeps its object.
def borrowed_names(v: Box, w: Box) -> None:
    t = (v, w)
    a = t[0]
    t = (w, v)  # tpyc: ok
    a.n += 100
    print("borrowed-names", a.n, t[0].n, v.n)


# borrowing call: the result points at the arguments, so its rebind re-points
# as the borrowed names' does.
def borrowing_call(v: Box, w: Box) -> None:
    t = (v, w)
    a = t[0]
    t = pick(w, v)  # tpyc: ok
    a.n += 1000
    print("borrowing-call", a.n, t[0].n, w.n)


# unpack of the BORROWED element, then the rebind: the target holds the lender,
# not the tuple's own object, so the rebind writes in place.
def unpack_borrowed(b: Box) -> None:
    p = make_mixed(1, b)
    _, a = p
    p = make_mixed(9, b)  # tpyc: ok
    a.n += 1
    print("unpack-borrowed", a.n, p[0].n, b.n)


# generator frame: a literal with a fresh element rebound across a suspension
# with nothing else naming the old tuple.
def frame_literal(b: Box) -> Iterator[int32]:
    t = (Box(1), b)
    for k in range(2):
        yield t[0].n
        t = (Box(10 + k), b)  # tpyc: ok
        t[1].n += 1
    yield t[0].n


def main() -> None:
    mixed_in_place(True)
    borrowed_names(Box(4), Box(5))
    borrowing_call(Box(6), Box(7))
    unpack_borrowed(Box(9))
    fb = Box(8)
    for n in frame_literal(fb):
        print("frame-literal", n, fb.n)


main()

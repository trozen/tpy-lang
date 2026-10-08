# A tuple global is a tuple of scalar globals: each reference element is the
# pointer slot a scalar reference global is (`std::tuple<Box*, Box*> M`), so
# the MIXED `M = make_mixed(V)` off `-> tuple[Own[Box], Box]`, the fully owned
# `G = make_owned(70)` and a literal with a fresh element park their value in
# a static of its own layout per module-level write -- as `V = Box(2)` parks
# its Box -- and point at it element-wise; a literal of names builds the
# pointer tuple in place. Each section writes through the borrowed element
# and prints the lender, so a silent copy would show. As for the scalar, a
# module-level rebind parks a fresh static (what was unpacked from the old
# tuple keeps it) and a last use at an owning slot is the warned copy.
import asyncio
from typing import Callable, Iterator

from mixmod import imixed, ilender, Box, make_mixed
from tpy import Own, copy, int32


def make_owned(n: int32) -> tuple[Own[Box], Own[Box]]:
    return (Box(n), Box(n + 1))


def make_whole(n: int32) -> Own[tuple[Box, Box]]:
    return (Box(n), Box(n + 1))


def bump(p: tuple[Box, Box]) -> None:
    p[1].n += 1


def relay() -> tuple[Box, Box]:
    # return place: the borrow slot lifts the owned element's address.
    return M  # tpyc: ok


# owning returns: the global owns nothing, so each owned slot takes the
# warned copy, as the scalar global does at `-> Own[Box]`.
def relay_mixed() -> tuple[Own[Box], Box]:
    return M  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)


def relay_owned() -> tuple[Own[Box], Own[Box]]:
    return G  # tpyc: warning(/tuple element 0/) warning(/tuple element 1/)


def pick(a: Box, b: Box) -> tuple[Box, Box]:
    return (b, a)


# a borrow-tuple local rebound from a borrowing call takes the call's
# pointer tuple bare, as the global below does.
def rebind_local(a: Box, b: Box) -> int32:
    t = (a, b)
    t[0].n += 1
    t = pick(a, b)  # tpyc: ok
    t[0].n += 10
    return t[1].n


def reader(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    p[1].n += 1000
    return p[0].n + p[1].n


def oreader(p: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    return p[0].n + p[1].n


class Guard:
    # `__del__` plus a required `__init__` argument: no zero-argument form.
    def __init__(self, n: int32) -> None:
        self.n = n

    def __del__(self) -> None:
        pass


def guarded(b: Box) -> tuple[Own[Guard], Box]:
    return (Guard(8), b)


class Ctx:
    def __enter__(self) -> int32:
        return 0

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False


V = Box(2)
W = Box(3)
# inferred, annotated, and a literal with a fresh element: one layout.
M = make_mixed(V)  # tpyc: ok
A: tuple[Box, Box] = make_mixed(W)  # tpyc: ok
L: tuple[Box, Box] = (Box(5), V)  # tpyc: ok
G = make_owned(70)


class User:
    k: int32

    # constructor
    def __init__(self) -> None:
        M[1].n += 1  # tpyc: ok
        self.k = M[0].n

    # method
    def poke(self) -> int32:
        M[1].n += 10  # tpyc: ok
        return M[1].n


# generator
def gen() -> Iterator[int32]:
    M[1].n += 100  # tpyc: ok
    yield M[1].n


# async
async def coro() -> int32:
    await asyncio.sleep(0)
    M[1].n += 1000  # tpyc: ok
    return M[1].n


# generator / async: a name bound from the global across a suspension is a
# frame field of its pointer slots, so writes through it reach the objects.
def gen_alias() -> Iterator[int32]:
    t = M  # tpyc: ok
    yield 0
    t[0].n += 300
    yield t[0].n


async def coro_alias() -> int32:
    t = M  # tpyc: ok
    await asyncio.sleep(0)
    t[0].n += 3000
    return t[0].n


def main() -> None:
    # free function: both elements written in place.
    M[1].n = 44  # tpyc: ok
    M[0].n = 7  # tpyc: ok
    print("function", V.n, M[0].n, M[1].n)
    # unpack: the owned target aliases the element, the borrowed one the lender.
    x, y = M  # tpyc: ok
    y.n = 45
    x.n = 8
    print("function-unpack", V.n, M[0].n)
    A[1].n = 46  # tpyc: ok
    print("annotated", W.n, A[0].n)
    # a select over two mixed globals aliases the chosen one.
    sel = M if V.n > 0 else A  # tpyc: ok
    sel[0].n = 99
    sel[1].n = 48
    print("select", M[0].n, A[0].n, V.n)
    L[1].n = 47  # tpyc: ok
    L[0].n = 10
    print("literal", V.n, L[0].n)
    u = User()
    print("ctor", V.n, u.k)
    print("method", u.poke(), V.n)
    for v in gen():
        print("generator", v, V.n)
    print("async", asyncio.run(coro()), V.n)
    print("generator-alias", list(gen_alias()), M[0].n)
    print("async-alias", asyncio.run(coro_alias()), M[0].n)

    # closure
    def inner() -> int32:
        M[1].n += 2  # tpyc: ok
        return M[1].n

    print("closure", inner(), V.n)
    # comprehension
    print("comprehension", [M[1].n + i for i in range(2)])  # tpyc: ok
    f: Callable[[], int32] = lambda: M[1].n
    print("lambda", f())
    try:
        M[1].n = 5  # tpyc: ok
    finally:
        print("finally", V.n)
    with Ctx():
        M[1].n = 6  # tpyc: ok
    print("with", V.n)
    # borrowing parameter: the owned element lends its address.
    bump(M)  # tpyc: ok
    print("borrow-param", V.n)
    e = M[1]  # tpyc: ok
    e.n = 50
    print("alias", V.n)
    r = relay()
    r[1].n = 51
    r[0].n = 52
    print("relay", V.n, M[0].n)
    # (the owned slots hold copies, so only the borrowed element is written
    # through: CPython would share the owned ones)
    rm = relay_mixed()
    rm[1].n += 1
    ro = relay_owned()
    print("relay-owning", V.n, rm[0].n, ro[0].n + ro[1].n)
    # a name bound from the global aliases both elements, as one bound from
    # a mixed local does.
    t = M  # tpyc: ok
    t[0].n = 53
    t[1].n = 54
    print("name-alias", V.n, M[0].n)
    loc = make_mixed(V)
    lt = loc  # tpyc: ok
    lt[0].n = 55
    print("local-alias", loc[0].n)
    # imported: the form comes off the binding the defining module recorded.
    imixed[1].n = 31  # tpyc: ok
    imixed[0].n = 9
    p, q = imixed  # tpyc: ok
    q.n += 1
    print("import", ilender.n, imixed[0].n, p.n)


def show() -> None:
    print("after", M[0].n, V.n, G[0].n)


# module level
M[1].n = 20  # tpyc: ok
print("module", V.n, M[0].n)
a, b = M  # tpyc: ok
b.n = 21
a.n = 22
print("module-unpack", V.n, M[0].n)
main()
# module-level last use at an owning slot: the global owns nothing, so the
# owned elements copy their referents in, warned.
print("last-use", reader(M))  # tpyc: warning(/copies tuple\[Box, Box\] into owned storage/)
print("last-use-owned", oreader(G))  # tpyc: warning(/copies tuple\[Box, Box\] into owned storage/)
show()
# module-level rebind after an unpack: the global parks a fresh static and
# re-points, so the unpacked names keep the old objects.
R = make_mixed(V)
ra, rb = R
R = make_mixed(W)  # tpyc: ok
ra.n = 80
R[0].n = 81
rb.n += 1
print("rebind-mixed", ra.n, R[0].n, V.n, R[1].n)
T = make_owned(90)
tx, ty = T
T = make_owned(95)  # tpyc: ok
tx.n += 1
print("rebind-owned", tx.n, T[0].n)
# a call owning its tuple WHOLE (`-> Own[tuple[Box, Box]]`) parks as the
# per-element owned one does.
WH = make_whole(60)  # tpyc: ok
WH[1].n += 1
print("whole-own", WH[0].n, WH[1].n)
# a name bound from the global copies its pointer slots, so it keeps the
# objects across a rebind of the global.
R2 = R  # tpyc: ok
R2[0].n = 82
R = make_mixed(V)
print("alias", R2[0].n, R[0].n)
# an element with no zero-argument form: the parked static is built from the
# init.
gm = guarded(V)  # tpyc: ok
go = (Guard(9), W)  # tpyc: ok
gm[1].n += 1
go[0].n += 1
print("no-default", gm[0].n, go[0].n, V.n, go[1].n)
# a one-element tuple global is the scalar global inside `std::tuple<...>`.
S = (Box(6),)  # tpyc: ok
S[0].n += 1
print("singleton", S[0].n)
# a literal with a borrowed element before a fresh one: the borrowed one stays
# a pointer in the parked static.
bf = (V, Box(7))  # tpyc: ok
bf[0].n += 1
bf[1].n += 1
print("borrowed-fresh", V.n, bf[1].n)
# a later literal with a fresh element into a global first bound from names
# parks, where the first write built the pointer tuple in place.
P = (V, 1)
P = (copy(W), 2)  # tpyc: ok
P[0].n += 100
print("later-fresh", P[0].n, W.n, P[1])
# an all-borrow rebind inside a module-level `for` parks nothing: each pass
# re-points the slots.
F = (V, 0)
for i in range(2):
    F = (W, i)  # tpyc: ok
F[0].n += 1
print("for-borrow", W.n, F[1])
# a parking rebind inside a module-level `if` parks a static of its own.
C = make_mixed(V)
if V.n > 0:
    C = make_mixed(W)  # tpyc: ok
C[1].n += 1
print("if-rebind", W.n, C[0].n)
# a module-level alias of an imported tuple global copies its pointer slots.
im = imixed  # tpyc: ok
im[0].n += 1
print("import-alias", imixed[0].n, ilender.n)
# a borrowing call's result is the pointer tuple itself, assigned bare, as
# the scalar's `V2 = pick_one(V)` points at the callee's result.
pc = pick(V, W)  # tpyc: ok
pc[0].n += 1
print("borrow-call", W.n, pc[1].n, rebind_local(V, W), V.n, W.n)

# A MIXED owned+borrow tuple param (`tuple[Own[Box], Box]`) is an ownership
# transfer like its fully owned twin (`std::tuple<Box, Box*>&&`): the owned
# element is the callee's to write, bind, unpack and return, and the borrowed
# element keeps pointing at the caller's object -- every section writes
# through it and `main` prints the caller's `b` to show the alias. A @nocopy
# owned element (`Tok`) makes any silent copy of the transfer a build error.
import asyncio
from typing import Callable, Iterator, Protocol

from tpy import Own, dynamic, int32, nocopy


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> int32:
        self.n += 1
        return self.n


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# Reads the box it is handed and drops it: the sections only need a sink.
def sink(b: Own[Box]) -> int32:  # tpyc: warning(/Own\[Box\] param 'b' is never consumed/)
    return b.n


def take(t: Own[Tok]) -> int32:
    return t.n


def mut(b: Box) -> None:
    b.n += 5


# free function: writes through both elements; the owned one also goes to a
# mutable parameter.
def free_write(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    p[0].n = 10
    mut(p[0])  # tpyc: ok
    p[1].n = 11
    return p[0].n


class H:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    # method: the unpack at the param's last use moves the owned element out
    # and binds the borrowed one to the caller's object.
    def unpack(self, p: tuple[Own[Tok], Box]) -> int32:  # tpyc: ok
        a, c = p  # tpyc: ok
        c.n = 22
        return take(a) + self.k


class Keeper:
    own: Tok

    # constructor: the unpacked owned element moves into the field.
    def __init__(self, p: tuple[Own[Tok], Box]) -> None:  # tpyc: ok
        a, c = p
        self.own = a
        c.n = 33


# closure: the nested def writes both elements of the captured param.
def closure_write(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    def g() -> None:
        p[0].n = 40
        p[1].n = 44
    g()
    return p[0].n


# generator: the frame takes the tuple over; a name bound to the owned
# element aliases it, and the unpack after the suspension moves it out.
def gen_write(p: tuple[Own[Tok], Box]) -> Iterator[int32]:  # tpyc: ok
    p[0].n = 50
    o = p[0]  # tpyc: ok
    o.n += 1
    p[1].n = 55
    yield p[0].n
    a, c = p
    c.n += 1
    yield take(a)


# async: same as the generator.
async def async_write(p: tuple[Own[Tok], Box]) -> int32:  # tpyc: ok
    p[0].n = 60
    p[1].n = 66
    await asyncio.sleep(0)
    a, c = p
    c.n += 1
    return take(a)


# generator expression: its frame captures `p`, reads the owned element and
# writes through the borrowed one.
def genexpr_write(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    return sum(p[0].n + p[1].bump() for _ in range(2))  # tpyc: ok


# generic: `tuple[Own[T], T]` takes the same transfer, and the borrowed element
# reaches the caller's object through a forwarding generic. (`a, c = p` in such
# a body still rejects at `stmt.tuple_unpack`.)
def g_inner[T](p: tuple[Own[T], T], f: Callable[[T], None]) -> None:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    f(p[1])


def g_forward[T](p: tuple[Own[T], T], f: Callable[[T], None]) -> None:  # tpyc: ok
    g_inner(p, f)


def set_seven(b: Box) -> None:
    b.n = 7


# @nocopy owned element: the unpack at the last use moves it out.
def nocopy_unpack(p: tuple[Own[Tok], Box]) -> int32:  # tpyc: ok
    t, c = p  # tpyc: ok
    c.n = 80
    return take(t)


def mk_tok(b: Box) -> tuple[Own[Tok], Box]:
    return (Tok(8), b)


def mixed_take(p: tuple[Own[Tok], Box]) -> int32:  # tpyc: ok
    p[1].n = 88
    return p[0].n


# a mixed local at its last use moves into the mixed param (a @nocopy owned
# element could not be copied).
def local_forward(b: Box) -> int32:
    u = mk_tok(b)
    return mixed_take(u)  # tpyc: ok


# a mixed local unpacked first: the borrowed target points at the caller's
# object, not into `u`, so `u` still moves at its last use.
def local_unpack_forward(b: Box) -> int32:
    u = mk_tok(b)
    _, c = u  # tpyc: ok
    c.n += 10
    return mixed_take(u)  # tpyc: ok


# a mixed local rebound on one branch still moves at its last use.
def local_rebound(b: Box, again: bool) -> int32:
    u = mk_tok(b)
    if again:
        u = mk_tok(b)
    return mixed_take(u)  # tpyc: ok



def mk_box(b: Box) -> tuple[Own[Box], Box]:
    return (Box(9), b)


def borrow_write(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    p[1].n = 90
    return p[0].n


# a fully owned tuple param still read afterwards: the call copies its owned
# element and points the borrowed one at the param's own element.
def owned_live(p: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    got = borrow_write(p)  # tpyc: warning(/copies tuple\[Own\[Box\], Own\[Box\]\] into owned storage/)
    return got + p[1].n


# closure + unpack: a nested def captures the param and runs in the `return`,
# so the unpack aliases the owned element instead of moving it out, and the
# writes through `a` and `c` show through the closure.
def closure_unpack(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    def g() -> int32:
        return p[0].n + p[1].n
    a, c = p  # tpyc: ok
    a.n = 100
    c.n = 101
    return g()


# ... the fully owned twin.
def closure_unpack_owned(p: tuple[Own[Box], Own[Box]]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    def g() -> int32:
        return p[0].n + p[1].n
    a, c = p  # tpyc: ok
    a.n = 102
    c.n = 103
    return g()


# ... a closure never called after the unpack: a name a closure captures is
# never moved, so the unpack still aliases the element.
def closure_unpack_uncalled(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    def g() -> int32:
        return p[0].n
    a, c = p  # tpyc: ok
    a.n = 104
    return a.n


# closure + forward: the param handed to an owning param while a closure
# still reads it copies (warned) instead of moving.
def closure_forward(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    def g() -> int32:
        return p[0].n + p[1].n
    got = borrow_write(p)  # tpyc: warning(/copies tuple\[Own\[Box\], Box\] into owned storage/)
    return got + g()


# return: the tuple's last use, so the owned element moves back to the
# caller and the param counts as consumed.
def give_back(p: tuple[Own[Box], Box]) -> Own[Box]:  # tpyc: ok
    p[1].n = 99
    return p[0]  # tpyc: ok


# the same return of a @nocopy owned element: a copy would not build.
def give_tok(p: tuple[Own[Tok], Box]) -> Own[Tok]:  # tpyc: ok
    p[1].n = 98
    return p[0]  # tpyc: ok


# the owned element returned out of a mixed LOCAL at its last use.
def give_local(b: Box) -> Own[Tok]:
    u = mk_tok(b)
    u[1].n = 97
    return u[0]  # tpyc: ok


def peek(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    p[1].n += 1
    return p[0].n + p[1].n


# generator expression, captured param handed on: the frame is pulled more
# than once, so the capture is never moved out of -- each pull copies the
# owned element and says so, like the scalar `Own[T]` capture.
def genexpr_param(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    return sum(peek(p) for _ in range(2))  # tpyc: warning(/copies tuple\[Own\[Box\], Box\] into owned storage/)


# ... and a captured mixed LOCAL handed on.
def genexpr_local(b: Box) -> int32:
    t = mk_box(b)
    return sum(peek(t) for _ in range(2))  # tpyc: warning(/copies tuple\[Own\[Box\], Box\] into owned storage/)


# operator shims: the friend `operator+` / `operator-` a dunder gets hands its
# ownership-transfer parameter on with `std::move`, tuple and scalar alike (TPy's
# `+` takes no tuple operand, so the C++ build is what checks the tuple one).
# A dunder is implicitly readonly, which reaches the borrowed element of its
# tuple parameter, so this one only reads it (the refused write is
# tuple/error_dunder_tuple_param_element_write).
class Adder:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __add__(self, o: tuple[Own[Tok], Box]) -> int32:  # tpyc: ok
        a, c = o
        return self.k + take(a) + c.n

    def __sub__(self, o: Own[Tok]) -> int32:  # tpyc: ok
        return self.k - take(o)


# @dynamic adapter: the override forwarding to a readonly implementation
# hands the transfer on the same way.
@dynamic
class Taker(Protocol):
    def pair(self, p: tuple[Own[Tok], Own[Tok]]) -> int32: ...

    def one(self, t: Own[Tok]) -> int32: ...


class TakerImpl:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def pair(self, p: tuple[Own[Tok], Own[Tok]]) -> int32:  # tpyc: ok
        a, c = p
        return self.k + take(a) + take(c)

    def one(self, t: Own[Tok]) -> int32:  # tpyc: ok
        return self.k + take(t)


def use_taker(t: Taker) -> int32:
    return t.pair((Tok(1), Tok(2))) + t.one(Tok(3))


# the fully owned twin in a method that only reads `self` (a const method) and
# in a constructor: the transfer holds there too, so @nocopy elements move out.
class TokPair:
    first: Tok

    def __init__(self, p: tuple[Own[Tok], Own[Tok]]) -> None:  # tpyc: ok
        a, c = p
        self.first = a

    def sum(self, p: tuple[Own[Tok], Own[Tok]]) -> int32:  # tpyc: ok
        a, c = p
        return take(a) + take(c) + self.first.n


def pair_take(p: tuple[Own[Tok], Own[Tok]]) -> int32:  # tpyc: ok
    a, c = p
    return take(a) + take(c)


# constructor member-init: the handed-over params, mixed and fully owned, move
# into the calls (a @nocopy owned element could not be copied).
class Init:
    n: int32
    m: int32

    def __init__(self, p: tuple[Own[Tok], Box], q: tuple[Own[Tok], Own[Tok]]) -> None:  # tpyc: ok
        self.n = mixed_take(p)  # tpyc: ok
        self.m = pair_take(q)  # tpyc: ok


# field write: the whole param stored into a tuple field moves its owned
# element and copies the borrowed one (warned -- a field owns its elements,
# so the copy is intended). The element-0 warning is spurious:
# BUGS.md#own-tuple-param-field-store-warns.
class Slot:
    pair: tuple[Box, Box]

    def __init__(self) -> None:
        self.pair = (Box(0), Box(0))

    def put(self, p: tuple[Own[Box], Box]) -> None:
        self.pair = p  # tpyc: warning(/copies Box into field \(tuple element 1\)/)


def main() -> None:
    b = Box(0)
    print("free", free_write((Box(1), b)), b.n)
    print("method", H().unpack((Tok(2), b)), b.n)
    k = Keeper((Tok(3), b))
    print("ctor", k.own.n, b.n)
    print("closure", closure_write((Box(4), b)), b.n)
    for v in gen_write((Tok(5), b)):
        print("generator", v, b.n)
    print("async", asyncio.run(async_write((Tok(6), b))), b.n)
    print("genexpr", genexpr_write((Box(7), b)), b.n)
    g_forward((Box(8), b), set_seven)
    print("generic", b.n)
    print("nocopy", nocopy_unpack((Tok(9), b)), b.n)
    print("local-last", local_forward(b), b.n)
    print("local-unpack", local_unpack_forward(b), b.n)
    print("local-rebound", local_rebound(b, True), local_rebound(b, False))
    print("owned-live", owned_live((Box(10), Box(11))))
    print("closure-unpack", closure_unpack((Box(0), b)), b.n)
    print("closure-unpack-owned", closure_unpack_owned((Box(0), Box(0))))
    print("closure-unpack-uncalled", closure_unpack_uncalled((Box(0), b)))
    print("closure-forward", closure_forward((Box(105), b)), b.n)
    print("return", give_back((Box(12), b)).n, b.n)
    print("return-nocopy", give_tok((Tok(17), b)).n, b.n)
    print("return-local", give_local(b).n, b.n)
    tp = TokPair((Tok(13), Tok(14)))
    print("owned-twin", tp.first.n, tp.sum((Tok(15), Tok(16))))
    print("genexpr-param", genexpr_param((Box(20), b)), b.n)
    print("genexpr-local", genexpr_local(b), b.n)
    print("operator", Adder(30) - Tok(4))
    print("adapter", use_taker(TakerImpl(40)))
    ini = Init((Tok(21), b), (Tok(22), Tok(23)))
    print("member-init", ini.n, ini.m, b.n)
    sl = Slot()
    sl.put((Box(24), b))
    print("field-write", sl.pair[0].n, sl.pair[1].n)


main()


def reader(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    return p[0].n + p[1].n


# module global: a mixed global holds storage form, so a generator expression
# capturing it reads its elements in place, and its last use lifts into the
# mixed param element by element.
GB = Box(71)
G = mk_box(GB)
print("global-genexpr", sum(G[0].n + G[1].n for _ in range(2)))  # tpyc: ok
print("global", reader(G))  # tpyc: ok

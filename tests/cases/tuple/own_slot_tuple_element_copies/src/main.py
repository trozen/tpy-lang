# Owning tuple slots: what moves in (Own elements, owning calls) and what a
# whole-tuple field store copies; mutations show copies.
from tpy import Own, copy, int32


class A:
    def __init__(self, x: int32) -> None:
        self.x = x


def mk(a: A) -> tuple[A, int32]:
    return (a, 1)


def mixed(a: A) -> tuple[Own[A], A]:
    return (A(a.x + 10), a)


def fresh(a: A) -> tuple[Own[A], int32]:
    return (A(a.x + 20), 1)


def take_elem(p: tuple[Own[A], int32]) -> Own[A]:
    return p[0]


def sink(p: Own[A]) -> Own[A]:
    p.x += 1
    return p


# free function: a local bound to an Own tuple parameter aliases it
def alias_param(p: Own[tuple[A, int32]]) -> int32:
    q = p  # tpyc: ok
    q[0].x = 9
    return p[0].x


# closure: a nested def reads the Own parameter it captures, no copy
def captured(p: Own[tuple[A, int32]]) -> int32:
    def read() -> int32:
        return p[0].x
    first = read()
    p[0].x = 9
    return first + read()


# free function: both arms own element 0, so the select-bound tuple owns it and
# its element moves into the Own parameter
def select_owned(c: bool, a: A) -> int32:
    s = mixed(a) if c else mixed(a)
    u = s
    u[0].x = 40
    print("select alias", s[0].x, s[1].x)
    return sink(s[0]).x  # tpyc: ok


# free function: a call that owns every reference element moves it into the
# Own element slot
def owning_call(a: A) -> int32:
    e = take_elem(fresh(a))  # tpyc: ok
    e.x += 1
    return e.x


# free function: the copy remedy, spelled through a local; `copy(mk(a))` does
# not lower yet (BUGS.md#copy-tuple-call-source)
def remedy(a: A) -> Own[tuple[A, int32]]:
    t = mk(a)
    return copy(t)  # tpyc: ok


def keep_both(q: tuple[Own[A], Own[A]]) -> Own[list[tuple[A, A]]]:
    ys: list[tuple[A, A]] = []
    ys.append(q)
    return ys


# free function: a mixed tuple parameter appended at its last use moves its
# Own element in; its declared type says element 1 arrives borrowed, so that
# copy warns (the sections only read the result back: the copy is the warned
# divergence)
def append_param(p: tuple[Own[A], A]) -> Own[list[tuple[A, A]]]:
    ys: list[tuple[A, A]] = []
    ys.append(p)  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)
    return ys


# free function: likewise forwarded whole to an `Own`-element argument
def forward_param(p: tuple[Own[A], A]) -> Own[list[tuple[A, A]]]:
    return keep_both(p)  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)


# free function: a mixed literal local appended at its last use warns its
# borrowed element 1 only, though the store copies element 0 too
# (BUGS.md#mixed-tuple-local-append-copies-owned)
def append_mixed_local(a: A) -> Own[list[tuple[A, A]]]:
    ys: list[tuple[A, A]] = []
    t = (A(5), a)
    ys.append(t)  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)
    return ys


class Holder:
    bp: tuple[A, A]

    def __init__(self) -> None:
        self.bp = (A(0), A(0))

    # method: a tuple local holding one element by reference is copied whole
    # into the field at its last use, so both elements warn; element 0's
    # warning is an over-warning, the store moves it
    # (BUGS.md#tuple-local-last-use-copies-owned-elements)
    def from_mixed(self, b: A) -> None:
        t = (A(7), b)
        self.bp = t  # tpyc: warning(/copies A into field \(tuple element 0\)/) warning(/copies A into field \(tuple element 1\)/)

    # method: likewise when the local is read after the store, where element
    # 0's copy is real (its last-use twin is
    # BUGS.md#tuple-local-last-use-copies-owned-elements)
    def from_mixed_live(self, b: A) -> int32:
        t = (A(8), b)
        self.bp = t  # tpyc: warning(/copies A into field \(tuple element 0\)/) warning(/copies A into field \(tuple element 1\)/)
        return t[0].x


def main() -> None:
    h = Holder()
    b = A(2)
    h.from_mixed(b)
    h.bp[0].x += 1
    print("from_mixed", h.bp[0].x, h.bp[1].x)
    print("from_mixed_live", h.from_mixed_live(b), h.bp[0].x)
    zs = append_param((A(3), b))
    print("append_param", zs[0][0].x, zs[0][1].x)
    vs = append_mixed_local(b)
    vs[0][0].x += 1
    print("append_mixed_local", vs[0][0].x, vs[0][1].x)
    ws = forward_param((A(4), b))
    print("forward_param", ws[0][0].x, ws[0][1].x)
    a = A(1)
    print("alias", alias_param((A(1), 1)))
    print("captured", captured((A(1), 2)))
    n = select_owned(True, a)
    print("select", n, a.x)
    print("owning_call", owning_call(a), a.x)
    r = remedy(a)
    r[0].x = 55
    print("remedy", a.x, r[0].x)


main()

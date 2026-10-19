# A tuple local with an open-T element holds the form its source holds, so a
# copy of it shares the element object with its source as in CPython.
from typing import Callable
from tpy import int32, Fn, Own, ValueType, nocopy, readonly, error_return, ReturnException


class A:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __eq__(self, other: "A") -> bool:
        return self is other


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


class Neg(Exception, ReturnException):
    pass


def bump(a: A) -> None:
    a.v += 10


def nop(a: int32) -> None:
    pass


def show(s: str) -> None:
    print("show", s)


def show_n(n: int32) -> None:
    print("escape", n)


def ident(n: int32) -> int32:
    return n


def mk[T](a: T) -> tuple[T, int32]:
    return (a, 1)


def mk_own[T](a: Own[T]) -> Own[tuple[T, int32]]:
    return (a, 2)


def mko[T](a: Own[T]) -> Own[T]:
    return a


# Each generic section runs at a class A (identity __eq__, mutated through the
# local before the source is printed) and at int32.
# free function, parameter source (also at str)
def s_param[T](x: tuple[T, int32], f: Fn[[T], None]) -> None:
    y = x  # tpyc: ok
    f(y[0])
    print("param", y[0] == x[0], y[1])


# borrow-returning call source
def s_call[T](a: T, f: Fn[[T], None]) -> None:
    c = mk(a)  # tpyc: ok
    f(c[0])
    print("call", c[0] == a, c[1])


# one-element and two-open-element tuples
def s_one[T](x: tuple[T], f: Fn[[T], None]) -> None:
    y = x  # tpyc: ok
    f(y[0])
    print("one", y[0] == x[0])


def s_two[T](x: tuple[T, T], f: Fn[[T], None]) -> None:
    y = x  # tpyc: ok
    f(y[1])
    print("two", y[0] == x[0], y[1] == x[1])


# readonly element
def s_ro[T](x: tuple[readonly[T], int32]) -> None:
    y = x  # tpyc: ok
    print("ro", y[0] == x[0], y[1])


# method and constructor
class K[T]:
    n: int32

    def __init__(self, x: tuple[T, int32]) -> None:
        y = x  # tpyc: ok
        print("ctor", y[0] == x[0])
        self.n = y[1]

    def g(self, x: tuple[T, int32], f: Fn[[T], None]) -> None:
        y = x  # tpyc: ok
        f(y[0])
        print("method", y[0] == x[0], self.n)


# closure body (one that outlives the body keeps the source's pointers:
# BUGS.md#escaping-closure-borrow-tuple-capture)
def s_closure[T](x: tuple[T, int32], f: Fn[[T], None]) -> None:
    def inner() -> None:
        y = x  # tpyc: ok
        f(y[0])
        print("closure", y[0] == x[0])
    inner()


# a local an escaping closure captures keeps the storage copy, which builds
# at a value T only (BUGS.md#escaping-closure-borrow-tuple-capture)
def s_escape[T](x: tuple[T, int32], b: Own[T], flag: bool,
                f: Fn[[T], None]) -> Callable[[], None]:
    o = mko(b)
    p = mk(o)
    y = p if flag else x  # tpyc: ok
    u = p  # tpyc: ok

    def h() -> None:
        f(y[0])
        f(u[0])
    return h


# an escaping lambda keeps the same storage copy, returned or stored
def s_escape_lam[T](x: tuple[T, int32], b: Own[T], flag: bool,
                    f: Fn[[T], int32]) -> Callable[[], int32]:
    o = mko(b)
    p = mk(o)
    y = p if flag else x  # tpyc: ok
    return lambda: f(y[0])


def s_escape_lam_store[T](x: tuple[T, int32], b: Own[T], flag: bool,
                          f: Fn[[T], int32],
                          out: list[Callable[[], int32]]) -> None:
    o = mko(b)
    p = mk(o)
    y = p if flag else x  # tpyc: ok
    out.append(lambda: f(y[0]))


# @error_return body
@error_return(Neg)
def s_er[T](x: tuple[T, int32], f: Fn[[T], None]) -> int32:
    y = x  # tpyc: ok
    f(y[0])
    print("er", y[0] == x[0])
    if y[1] < 0:
        raise Neg
    return y[1]


# if and match blocks
def s_if[T](x: tuple[T, int32], flag: bool, f: Fn[[T], None]) -> None:
    if flag:
        y = x  # tpyc: ok
        f(y[0])
        print("if", y[0] == x[0])


def s_match[T](x: tuple[T, int32], k: int32, f: Fn[[T], None]) -> None:
    match k:
        case 1:
            y = x  # tpyc: ok
            f(y[0])
            print("match", y[0] == x[0])
        case _:
            print("match none")


# reassigned and branch-hoisted
def s_reseat[T](x: tuple[T, int32], z: tuple[T, int32],
                f: Fn[[T], None]) -> None:
    y = x  # tpyc: ok
    f(y[0])
    y = z  # tpyc: ok
    f(y[0])
    print("reseat", y[0] == z[0], y[1])


def s_hoist[T](x: tuple[T, int32], z: tuple[T, int32], flag: bool,
               f: Fn[[T], None]) -> None:
    if flag:
        y = x  # tpyc: ok
    else:
        y = z  # tpyc: ok
    f(y[0])
    print("hoist", y[0] == x[0], y[0] == z[0])


# a reseat from a borrow call, a storage local, a loop variable or a select
# keeps the storage copy, and so does a copy or a select of that local after
# it, so these run at a value T only
# (BUGS.md#open-t-tuple-reseat-storage-residue)
def s_residue[T](x: tuple[T, int32], a: T, b: Own[T],
                 xs: list[tuple[T, int32]], flag: bool) -> int32:
    w = mk_own(b)
    y = x
    n = y[1]
    y = mk(a)  # tpyc: ok
    n += y[1]
    y = w  # tpyc: ok
    n += y[1]
    for q in xs:
        y = q  # tpyc: ok
        n += y[1]
    y = x if flag else w  # tpyc: ok
    n += y[1]
    u = y  # tpyc: ok
    v = y if flag else x  # tpyc: ok
    return n + u[1] + v[1]


# a reseat from a storage local at its last use moves it in; one a later loop
# pass or a later read still needs keeps the copy (run at str, read off y[0])
def s_move_last[T](x: tuple[T, int32], c: Own[T], f: Fn[[T], None]) -> None:
    w = mk_own(c)
    y = x
    y = w  # tpyc: ok
    f(y[0])


def s_move_loop[T](x: tuple[T, int32], c: Own[T], f: Fn[[T], None]) -> None:
    w = mk_own(c)
    y = x
    for i in range(2):
        y = w  # tpyc: ok
        f(y[0])


def s_move_continue[T](x: tuple[T, int32], c: Own[T],
                       f: Fn[[T], None]) -> None:
    w = mk_own(c)
    y = x
    for i in range(2):
        y = w  # tpyc: ok
        f(y[0])
        continue


def s_move_branch[T](x: tuple[T, int32], c: Own[T], flag: bool,
                     f: Fn[[T], None]) -> None:
    w = mk_own(c)
    y = x
    if flag:
        y = w  # tpyc: ok
    f(y[0])
    f(w[0])


# an open T beside a str keeps the storage copy, which builds at a value T
# only (BUGS.md#tuple-select-deferred-shapes)
def s_str_elem[T](x: tuple[T, str]) -> int32:
    y = x  # tpyc: ok
    return len(y[1])


# storage source bound to a second name, and a loop variable over storage
def s_store[T](a: Own[T], f: Fn[[T], None]) -> None:
    c = mk_own(a)
    d = c  # tpyc: ok
    f(d[0])
    print("store", c[0] == d[0], d[1])


def s_loop[T](xs: list[tuple[T, int32]], f: Fn[[T], None]) -> None:
    for q in xs:
        z = q  # tpyc: ok
        f(z[0])
        print("loop", z[0] == q[0], z[1])


# a select of two borrow names
def s_sel[T](x: tuple[T, int32], z: tuple[T, int32], flag: bool,
             f: Fn[[T], None]) -> None:
    y = x if flag else z  # tpyc: ok
    f(y[0])
    print("sel", y[0] == x[0], y[0] == z[0])


# every other select keeps the storage copy -- a storage arm, a nested
# select -- so these run at a value T only
# (BUGS.md#tuple-select-deferred-shapes)
def s_sel_storage[T](x: tuple[T, int32], z: tuple[T, int32], b: Own[T],
                     c: Own[T], flag: bool, other: bool) -> int32:
    w = mk_own(b)
    w2 = mk_own(c)
    m = x if flag else w  # tpyc: ok
    s = w if flag else w2  # tpyc: ok
    n = x if flag else (z if other else x)  # tpyc: ok
    return m[1] + s[1] + n[1]


# a branch-bound local under try / finally and try / except
def s_try[T](x: tuple[T, int32], z: tuple[T, int32], k: int32,
             f: Fn[[T], None]) -> None:
    try:
        y = x  # tpyc: ok
    finally:
        print("try fin")
    f(y[0])
    try:
        if k == 0:
            raise ValueError("e")
        u = x  # tpyc: ok
    except ValueError:
        u = z  # tpyc: ok
    f(u[0])
    print("try", y[0] == x[0], u[0] == x[0], u[0] == z[0])


# a value-bounded T has one form: the plain tuple copy
class V[T: ValueType]:
    def g(self, x: tuple[T, int32]) -> int32:
        y = x  # tpyc: ok
        return y[1]


# a @nocopy element: the storage alias is no copy
def s_nocopy[T](a: Own[T]) -> int32:
    c = mk_own(a)
    d = c  # tpyc: ok
    return d[1]


# concrete twins of the shapes admitted beside the generic ones
def c_if(x: tuple[A, int32], flag: bool) -> None:
    if flag:
        y = x  # tpyc: ok
        bump(y[0])
        print("c_if", y[0] == x[0])


def c_reseat(x: tuple[A, int32], z: tuple[A, int32]) -> None:
    y = x  # tpyc: ok
    bump(y[0])
    y = z  # tpyc: ok
    bump(y[0])
    print("c_reseat", y[0] == z[0], y[1])


# a function-level alias and select of tuple[A, str] keep their render (the
# str is copied, as before); in a block, a reseat or a hoist they are refused
def c_str(x: tuple[A, str], z: tuple[A, str], flag: bool) -> None:
    y = x  # tpyc: ok
    bump(y[0])
    u = x if flag else z  # tpyc: ok
    bump(u[0])
    print("c_str", y[0] == x[0], u[0] == z[0], y[1], u[1])


# a parameter reseated to a tuple literal of a name
def c_reseat_lit(pair: tuple[A, int32], other: A) -> None:
    saved = pair  # tpyc: ok
    bump(saved[0])
    saved = (other, 2)  # tpyc: ok
    bump(saved[0])
    print("c_reseat_lit", saved[0] == other, pair[0].v, other.v)


def c_hoist(x: tuple[A, int32], z: tuple[A, int32], flag: bool) -> None:
    if flag:
        y = x  # tpyc: ok
    else:
        y = z  # tpyc: ok
    bump(y[0])
    print("c_hoist", y[0] == x[0], y[0] == z[0])


def c_loop() -> None:
    xs = [(A(1), 1), (A(2), 2)]
    for q in xs:
        z = q  # tpyc: ok
        bump(z[0])
        print("c_loop", z[0] == q[0], z[1])
    print("c_loop src", xs[0][0].v, xs[1][0].v)


# a select of two owned-element tuple locals is the plain tuple copy, which
# copies the selected elements, so this section only reads them
# (BUGS.md#tuple-select-storage-copy-silent)
def mk_pair(n: int32) -> tuple[Own[A], Own[A]]:
    return (A(n), A(n + 1))


def c_sel_owned(flag: bool) -> None:
    c = mk_pair(1)
    d = mk_pair(3)
    y = c if flag else d  # tpyc: ok
    print("c_sel_owned", y[0].v, y[1].v)


def main() -> None:
    a = A(3)
    b = A(4)
    s_param((a, 1), bump)
    s_param((5, 1), nop)
    s_param(("hi", 1), show)
    s_call(a, bump)
    s_call(5, nop)
    s_one((a,), bump)
    s_one((5,), nop)
    s_two((a, b), bump)
    s_two((5, 6), nop)
    s_ro((a, 1))
    s_ro((5, 1))
    k = K((a, 7))
    k.g((a, 1), bump)
    ki = K((5, 7))
    ki.g((5, 1), nop)
    s_closure((a, 1), bump)
    s_closure((5, 1), nop)
    s_escape((5, 1), 6, True, show_n)()
    s_escape((5, 1), 6, False, show_n)()
    print("escape_lam", s_escape_lam((5, 1), 6, True, ident)(),
          s_escape_lam((5, 1), 6, False, ident)())
    lams: list[Callable[[], int32]] = []
    s_escape_lam_store((5, 1), 7, True, ident, lams)
    s_escape_lam_store((5, 1), 7, False, ident, lams)
    print("escape_lam store", lams[0](), lams[1]())
    try:
        e1 = s_er((a, 3), bump)
        e2 = s_er((5, 4), nop)
        print("er", e1, e2)
    except Neg:
        print("er neg")
    s_if((a, 1), True, bump)
    s_if((5, 1), True, nop)
    s_match((a, 1), 1, bump)
    s_match((5, 1), 1, nop)
    s_reseat((a, 1), (b, 2), bump)
    s_reseat((5, 1), (6, 2), nop)
    s_hoist((a, 1), (b, 2), True, bump)
    s_hoist((a, 1), (b, 2), False, bump)
    s_hoist((5, 1), (6, 2), True, nop)
    s_store(A(9), bump)
    s_store(9, nop)
    xs = [(A(1), 1), (A(2), 2)]
    s_loop(xs, bump)
    s_loop([(5, 1)], nop)
    print("loop src", xs[0][0].v, xs[1][0].v)
    s_sel((a, 1), (b, 2), True, bump)
    s_sel((a, 1), (b, 2), False, bump)
    s_sel((5, 1), (6, 2), False, nop)
    print("sel_storage", s_sel_storage((5, 1), (6, 2), 7, 8, False, True))
    s_try((a, 1), (b, 2), 0, bump)
    s_try((a, 1), (b, 2), 1, bump)
    s_try((5, 1), (6, 2), 1, nop)
    print("residue", s_residue((5, 4), 6, 7, [(8, 8)], True))
    s_move_last(("in", 1), "last", show)
    s_move_loop(("in", 1), "loop", show)
    s_move_continue(("in", 1), "continue", show)
    s_move_branch(("in", 1), "branch", True, show)
    s_move_branch(("in", 1), "branch", False, show)
    print("str_elem", s_str_elem((5, "abc")))
    print("value_bounded", V[int32]().g((5, 1)))
    print("nocopy", s_nocopy(N(1)))
    c_if((a, 1), True)
    c_reseat((a, 1), (b, 2))
    c_str((a, "p"), (b, "q"), False)
    c_reseat_lit((a, 1), b)
    c_hoist((a, 1), (b, 2), True)
    c_hoist((a, 1), (b, 2), False)
    c_loop()
    c_sel_owned(True)
    c_sel_owned(False)
    print("src", a.v, b.v)


main()

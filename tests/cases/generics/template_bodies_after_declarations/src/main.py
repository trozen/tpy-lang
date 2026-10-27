# Template member bodies (members of a generic class, member templates,
# @overload variants) may use classes, functions and frames declared later.
from __future__ import annotations

from enum import Enum
from typing import (
    Any, Final, Iterable, Iterator, Literal, Protocol, Self, overload,
)
from tpy import Fn, dispatch, int32, Own, copy, readonly
import cyc_a
import cyc_user
import iters


# (a) generic class above the class and the function its body uses; the
# function names a nested type and defaults to a later class's constant
class Gen[T]:
    def __init__(self, x: Own[T]) -> None:
        self.x = x

    def m(self) -> int32:
        i = Outer.In(3)
        first = takes(i)  # tpyc: ok
        i.k = 10
        return first + takes(i)


# (b) generic method constructing a later class; another one mutates it
class Maker[T]:
    def __init__(self, step: int32) -> None:
        self.step = step

    def make(self, v: int32) -> Own[Later]:
        return Later(v)  # tpyc: ok

    def bump(self, l: Later) -> None:
        l.v += self.step


class HasN(Protocol):
    def n(self) -> int32: ...


# (c) member templates of a plain class: own type param, protocol param
class Plain:
    base: int32

    def __init__(self) -> None:
        self.base = 100

    def m[U](self, x: U) -> Own[Later]:
        return Later(self.base)  # tpyc: ok

    def p(self, h: HasN) -> Own[Later]:
        return Later(h.n())  # tpyc: ok


# (d) @overload method on a plain class calling a later function
class Ov:
    @overload
    def f(self, x: int32) -> int32: ...
    @overload
    def f(self, x: str) -> int32: ...
    def f(self, x: int32 | str) -> int32:
        if isinstance(x, str):
            return takes(Outer.In(len(x)))  # tpyc: ok
        return takes(Outer.In(x))


# (e) generic ctor + __copy__ + __del__, each using later declarations; one
# printing object per scope (BUGS.md#locals-destroyed-reverse-creation-order)
class Res[T]:
    n: int32

    def __init__(self, x: Own[T], tag: str) -> None:
        self.x = x
        self.tag = tag
        self.n = Later(1).v  # tpyc: ok

    def __copy__(self) -> Own[Res[T]]:
        x = copy(self.x)
        return Res(x, self.tag + "+copy")

    def __del__(self) -> None:
        note("e del " + self.tag)  # tpyc: ok


# (f) member template of a generic class (class head + own head)
class Pair[T]:
    def __init__(self, x: Own[T]) -> None:
        self.x = x

    def with_[U](self, u: U) -> Own[Later]:
        return Later(7)  # tpyc: ok


# (g) generic __getitem__ returning a later class: the operator[] shim
class Shelf[T]:
    def __init__(self, x: Own[T]) -> None:
        self.x = x

    def __getitem__(self, i: int32) -> Own[Later]:
        return Later(i)  # tpyc: ok


# (h) generic body iterating a free generator and building a recursive
# union, both declared later
class Walker[T]:
    def __init__(self, x: Own[T]) -> None:
        self.x = x

    def total(self) -> int32:
        t = 0
        for v in count_to(4):  # tpyc: ok
            t += v
        return t

    def leaves(self) -> str:
        tree: Tree = [1, [2, 3]]  # tpyc: ok
        before = count_leaves(tree)
        if isinstance(tree, list):
            tree.append(4)
        return f"{before} {count_leaves(tree)}"


# inverse: a deduced-return __iter__ keeps its body in the struct, so a later
# record's constraint check and a free generator's frame can use it
class Bag[T]:
    items: list[T]

    def __init__(self, x: Own[T], y: Own[T]) -> None:
        self.items = [x, y]

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)


class Holder[T: Iterable[int32]]:
    def __init__(self, t: Own[T]) -> None:
        self.t = t


class UsesHolder:
    h: Holder[Bag[int32]]

    def __init__(self) -> None:
        self.h = Holder(Bag(3, 4))  # tpyc: ok


def walk(b: Bag[int32]) -> Iterator[int32]:
    for v in b:  # tpyc: ok
        yield v


# (j) a plain class's @overload __iter__ (deduced return) iterated by an
# earlier class: the variants keep their bodies in the struct
class Summer:
    def total(self, c: Counts) -> int32:
        n = 0
        for v in c:  # tpyc: ok
            n += v
        return n


class Counts:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [3, 4]

    @overload
    def __iter__(self) -> Iterator[int32]: ...
    @overload
    def __iter__(self, mode: Literal[1]) -> Iterator[int32]: ...
    def __iter__(self, mode: int32 = 0) -> Iterator[int32]:
        return iter(self.items)


# (k) one @overload group, two placements: the deduced-return variant in the
# struct, the other at the end of the header, where it builds a later class
class Mixed[T]:
    items: list[int32]

    def __init__(self, x: Own[T]) -> None:
        self.x = x
        self.items = [3, 4]

    @overload
    def __iter__(self, mode: Literal[True]) -> Iterator[int32]: ...
    @overload
    def __iter__(self, mode: Literal[False]) -> int32: ...
    def __iter__(self, mode: bool) -> Any:
        if mode:
            return iter(self.items)
        return Later(7).v  # tpyc: ok


# (l) a generic class's hidden friend operators (binary, reflected, unary)
# hand back the borrowed slot itself: a later write through the class shows
class Slot:
    def __init__(self, n: int32) -> None:
        self.n = n


class Ops[T]:
    def __init__(self, x: Own[Slot], tag: Own[T]) -> None:
        self.x = x
        self.tag = tag

    def __add__(self, n: int32) -> readonly[Slot]:
        return self.x

    def __radd__(self, n: int32) -> readonly[Slot]:
        return self.x

    def __neg__(self) -> readonly[Slot]:
        return self.x


# (m) a plain class's member template over an `Fn` parameter, building a
# later class
class Applier:
    def apply(self, f: Fn[[int32], int32], x: int32) -> Own[Later]:
        return Later(f(x))  # tpyc: ok


# (n) a generic class the header defines early (`Eats`, the bound of a class a
# protocol names, names it), whose method calls a function and builds a class
# declared later (its signature naming one is
# BUGS.md#early-record-signature-names-later-class)
class Seed[T]:
    def __init__(self, x: Own[T]) -> None:
        self.x = x

    def grow(self, k: int32) -> int32:
        l = Later(tenfold(k))  # tpyc: ok
        l.v += 1
        return l.v


class Eats(Protocol):
    def eat(self, s: Seed[int32]) -> int32: ...


class Keeper[K: Eats]:
    def __init__(self, k: Own[K]) -> None:
        self.k = k

    def feed(self, s: Seed[int32]) -> int32:
        return self.k.eat(s)


class Feeds(Protocol):
    def keep(self, k: Keeper[Eater]) -> int32: ...


class Eater:
    def eat(self, s: Seed[int32]) -> int32:
        return s.grow(s.x)


# (p) a plain class's friend operators stay hidden friends: `b == a` (EqB
# never handles an EqA) still reflects to EqA.__eq__ beside EqA's `!=`
class EqA:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    @dispatch
    def __eq__(self, o: EqA) -> bool:
        return self.n == o.n

    @dispatch
    def __eq__(self, o: EqB) -> bool:
        return self.n == o.m

    @dispatch
    def __ne__(self, o: EqA) -> bool:
        return self.n != o.n

    @dispatch
    def __ne__(self, o: EqB) -> bool:
        return self.n != o.m


class EqB:
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


def same(x: EqA | EqB, y: EqA | EqB) -> bool:
    return x == y  # tpyc: ok


def differ(x: EqA | EqB, y: EqA | EqB) -> bool:
    return x != y


# (q) an @overload stub and its implementation spelling the class as `Self`
# on one side and by name on the other (a class, both ways, and an enum)
class SelfStub:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    @overload
    def f(self, x: Self) -> int32: ...
    def f(self, x: SelfStub) -> int32:  # tpyc: ok
        return self.k + x.k


class SelfImpl:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    @overload
    def f(self, x: SelfImpl) -> int32: ...
    def f(self, x: Self) -> int32:  # tpyc: ok
        return self.k * x.k


class Hue(Enum):
    RED = 1

    @overload
    def f(self, x: Self) -> int32: ...
    def f(self, x: Hue) -> int32:  # tpyc: ok
        return 9


class Later:
    def __init__(self, v: int32) -> None:
        self.v = v


class Outer:
    BONUS: Final[int32] = 1

    class In:
        def __init__(self, k: int32) -> None:
            self.k = k


class Five:
    def n(self) -> int32:
        return 5


type Tree = int32 | list[Tree]


def takes(i: Outer.In, bonus: int32 = Outer.BONUS) -> int32:
    return i.k + bonus


def note(s: str) -> None:
    print(s)


def tenfold(k: int32) -> int32:
    return k * 10


def count_to(n: int32) -> Iterator[int32]:
    i = 0
    while i < n:
        yield i
        i += 1


def count_leaves(t: Tree) -> int32:
    if isinstance(t, list):
        n = 0
        for c in t:
            n += count_leaves(c)
        return n
    return 1


def section_e_copy(r: Res[int32]) -> None:
    c = copy(r)
    c.tag = c.tag + "!"
    print("e copy", c.tag, c.n)


def section_e() -> None:
    r = Res(1, "a")
    section_e_copy(r)
    r.tag = "a2"
    print("e orig", r.tag)


def main() -> None:
    print("a", Gen(2).m())
    mk = Maker[int32](3)
    l = mk.make(4)
    mk.bump(l)
    l.v += 1
    mk.bump(l)
    print("b", l.v)
    pl = Plain()
    lm = pl.m(1.5)
    lm.v += 1
    lp = pl.p(Five())
    lp.v += 1
    print("c", lm.v, lp.v)
    ov = Ov()
    print("d", ov.f(4), ov.f("hey"))
    section_e()
    lf = Pair(1).with_("u")
    lf.v += 1
    print("f", lf.v)
    sh = Shelf(1)
    # a fresh owned value: nothing aliases it (binding it to a local or a
    # mutated parameter is BUGS.md#own-getitem-result-local-rejects /
    # BUGS.md#own-getitem-result-at-mutated-param-unhoisted)
    print("g", sh[6].v)
    w = Walker(1)
    print("h", w.total(), w.leaves())
    u = UsesHolder()
    u.h.t.items.append(7)
    s = len(u.h.t.items)
    for v in walk(Bag(5, 6)):
        s += v
    print("inverse", s)
    # (i) import-cycle member's @overload method calling the peer
    print("i", cyc_a.use_plain())
    c = Counts()
    sm = Summer()
    first = sm.total(c)
    c.items.append(5)
    print("j", first, sm.total(c))
    print("k", Mixed(1).__iter__(False))
    o = Ops(Slot(3), 1)
    a = o + 0
    b = 0 + o
    n = -o
    o.x.n = 9
    print("l", a.n, b.n, n.n)
    lm2 = Applier().apply(lambda v: v * 2, 3)
    lm2.v += 1
    print("m", lm2.v)
    kp = Keeper(Eater())
    sd = Seed(2)
    print("n", kp.feed(sd))
    print("cycle", cyc_a.use_cycle())
    print("cycle-iter", cyc_a.use_bag())
    print("cycle-groups", cyc_a.use_groups())
    print("cycle-ops", cyc_a.use_operators())
    print("cycle-passing", cyc_a.use_passing())
    # overload candidates naming the peer, used from outside the cycle
    print("cycle-user", cyc_user.use())
    # (o) a plain class's deduced `__iter__` defined in another module's
    # header, its body building a class defined after it
    st = iters.Stock()
    total = 0
    for v in st:
        total += v
    st.items.append(1)
    print("o", total, len(st.items))
    ea = EqA(1)
    eb = EqB(1)
    print("p", same(ea, eb), same(eb, ea), differ(eb, ea))
    eb.m = 2
    print("p", same(eb, ea), differ(eb, ea))
    s1 = SelfStub(1)
    s2 = SelfStub(2)
    first_q = s1.f(s2)
    s2.k = 5
    print("q", first_q, s1.f(s2), SelfImpl(3).f(SelfImpl(4)),
          Hue.RED.f(Hue.RED))


main()

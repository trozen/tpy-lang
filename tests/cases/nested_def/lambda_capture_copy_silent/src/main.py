# Closures whose captures match CPython: the remedies for the capture-copy
# warning (no copy, or a spelled one), value captures, and the shapes where
# the C++ may share the object, which the warning words as "may".
from typing import Callable
from tpy import int32, Fn, Ptr, Span, copy


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def call_now(f: Callable[[], int32]) -> int32:
    return f()


def apply_fn(f: Fn[[], int32]) -> int32:
    return f()


# nested_def: a nested def that does not escape reads the local in place
def nested_def() -> None:
    xs = [1]

    def size() -> int32:  # tpyc: ok
        return len(xs)
    xs.append(2)
    print("nested_def:", size())


# fn: an Fn parameter takes the lambda by reference, so it reads the local
def fn() -> None:
    xs = [1]
    xs.append(2)
    print("fn:", apply_fn(lambda: len(xs)))  # tpyc: ok


# typed_arg: the callback takes the object as its parameter
def typed_arg() -> None:
    xs = [1]
    read: Callable[[list[int32]], int32] = lambda ys: len(ys)  # tpyc: ok
    xs.append(2)
    print("typed_arg:", read(xs))


# snapshot: a spelled copy captured by an escaping nested def, which moves it
def snapshot(xs: list[int32]) -> Callable[[], int32]:
    snap = copy(xs)

    def read() -> int32:  # tpyc: ok
        return len(snap)
    return read


# values: an int32 and a view of a str parameter are copied as values
def values(s: str) -> None:
    k = 4
    print("values:", call_now(lambda: k + len(s)))  # tpyc: ok


# self: a method's lambda captures the receiver itself
class Counter:
    k: int32

    def __init__(self) -> None:
        self.k = 1

    def reader(self) -> Callable[[], int32]:
        return lambda: self.k  # tpyc: ok


# pointer_local: a rebound reference local is held by pointer, so the lambda
# shares it; the wording hedges
def pointer_local(c: bool) -> None:
    xs = [1]
    if c:
        xs = [5]
    f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/may keep its own copy of 'xs'/)
    xs.append(2)
    print("pointer_local:", f())


# pointer_def: the same rebound local captured by an escaping nested def
# keeps the def's reason, hedged
def pointer_def(c: bool) -> None:
    fs: list[Callable[[], int32]] = []
    xs = [1]
    if c:
        xs = [5]

    def size() -> int32:  # tpyc: warning(/Escaping closure 'size' may keep its own copy of local 'xs' \(used after closure definition/)
        return len(xs)
    fs.append(size)
    xs.append(2)
    print("pointer_def:", fs[0]())


# optional_param: an Optional reference parameter is a pointer
def optional_param(o: P | None) -> Callable[[], int32]:
    return lambda: o.n if o is not None else -1  # tpyc: warning(/may keep its own copy of 'o'/)


# borrow_tuple: a tuple of existing records holds pointers to them
def borrow_tuple(p1: P, p2: P) -> None:
    t = (p1, p2)
    f: Callable[[], int32] = lambda: t[0].n + t[1].n  # tpyc: warning(/may keep its own copy of 't'/)
    p1.n = 10
    print("borrow_tuple:", f())


# hoisted_loop: a loop-body local kept past its iteration shares one slot,
# so every lambda reads the last one, as CPython's do -- no loop warning
def hoisted_loop() -> None:
    fs: list[Callable[[], int32]] = []
    keep = P(0)
    for i in range(2):
        x = P(i)
        keep = x  # tpyc: warning(/'keep' will not keep the object/)
        fs.append(lambda: x.n)  # tpyc: warning(/may keep its own copy of 'x'/)
    print("hoisted_loop:", fs[0](), fs[1](), keep.n)


# ptr / span: a Ptr or a Span holds no object of its own, so the lambda
# sees the object through it, as CPython's does -- no warning
def ptr_span(xs: Span[P]) -> None:
    p = P(1)
    q: Ptr[P] = p
    f: Callable[[], int32] = lambda: q.n + xs[0].n  # tpyc: ok
    p.n = 10
    xs[0].n = 20
    print("ptr_span:", f())


# optional_cost: an int | None capture copies the int it holds
def optional_cost(k: int | None) -> Callable[[], bool]:
    return lambda: k is None  # tpyc: warning(/lambda at line 136 copies 'k' when it is created/)


# views: an Optional str parameter and a str literal local are views -- no
# warning
def views(s: str | None) -> None:
    v = "abc"
    print("views:", call_now(lambda: len(v) + (len(s) if s is not None else 0)))  # tpyc: ok


# rebound_param: a str parameter the body rebinds holds a string of its own,
# which the lambda copies
def rebound_param(s: str, c: bool) -> Callable[[], int32]:
    if c:
        s = s + "x"
    return lambda: len(s)  # tpyc: warning(/copies 's' when it is created/)


# ptr_union: a union of reference types at a parameter is held by pointer
class Q:
    def __init__(self, m: int32) -> None:
        self.m = m


def ptr_union(u: P | Q) -> Callable[[], int32]:
    return lambda: u.n if isinstance(u, P) else -1  # tpyc: warning(/may keep its own copy of 'u'/)


# loop_optional / outer_slot / outer_slot_rec: an Optional or reference
# local the loop binds lives in one slot each pass writes in place, so every
# lambda reads the last value, as CPython's do; the loop warning hedges
def loop_optional() -> None:
    p: P | None = P(8)
    fs: list[Callable[[], int32]] = []
    for i in range(2):
        p = P(i)
        fs.append(lambda: p.n if p is not None else -1)  # tpyc: warning(/may keep its own copy of 'p'/) warning(/'p' may be bound again on each iteration; the lambda may keep/)
    print("loop_optional:", fs[0](), fs[1]())


def outer_slot() -> None:
    fs: list[Callable[[], int32]] = []
    xs = [0]
    for i in range(3):
        xs = [i, i]
        fs.append(lambda: len(xs) * 10 + xs[0])  # tpyc: warning(/may keep its own copy of 'xs'/) warning(/'xs' may be bound again on each iteration/)
    print("outer_slot:", fs[0](), fs[1](), fs[2]())


def outer_slot_rec() -> None:
    fs: list[Callable[[], int32]] = []
    p = P(0)
    for i in range(3):
        p = P(i + 1)
        fs.append(lambda: p.n)  # tpyc: warning(/may keep its own copy of 'p'/) warning(/'p' may be bound again on each iteration/)
    p.n = 50
    print("outer_slot_rec:", fs[0](), fs[1](), fs[2]())


# union_param: an int32 | str parameter is passed by reference, not as a
# view, so the lambda copies the string it holds
def union_param(s: int32 | str) -> Callable[[], bool]:
    return lambda: isinstance(s, str)  # tpyc: warning(/copies 's' when it is created/)


# tuple_cost: a tuple holding an int | None copies the int
def tuple_cost(k: int | None) -> Callable[[], bool]:
    t = (k,)
    return lambda: t[0] is None  # tpyc: warning(/copies 't' when it is created/)


# union_local: an int | str local copies whichever it holds
def union_local(c: bool) -> None:
    k: int | str = 3
    if c:
        k = "abc"
    f: Callable[[], bool] = lambda: isinstance(k, str)  # tpyc: warning(/copies 'k' when it is created/)
    print("union_local:", f())


# callable: a captured Callable may hold a stateful object the lambda copies,
# so the wording hedges; this one holds none, so the output matches
def callable_cap() -> None:
    mk: Callable[[], list[int32]] = lambda: [1]
    f: Callable[[], int32] = lambda: len(mk())  # tpyc: warning(/may keep its own copy of 'mk'/)
    print("callable:", f())


# alias_opt: an escaping def over an Optional alias of an element holds the
# pointer; the def keeps its alias reason, hedged
def alias_opt(ps: list[P], c: bool) -> Callable[[], int32]:
    o = ps[0] if c else None

    def get() -> int32:  # tpyc: warning(/Escaping closure 'get' may keep its own copy of local 'o', which aliases storage it does not own/)
        return o.n if o is not None else -1
    return get


def main() -> None:
    nested_def()
    fn()
    typed_arg()
    xs = [1]
    r = snapshot(xs)
    xs.append(2)
    print("snapshot:", r(), len(xs))
    values("ab")
    c = Counter()
    g = c.reader()
    c.k = 7
    print("self:", g())
    pointer_local(True)
    pointer_def(True)
    p = P(1)
    h = optional_param(p)
    p.n = 8
    print("optional_param:", h())
    borrow_tuple(P(1), P(2))
    hoisted_loop()
    ys = [P(1)]
    ptr_span(ys)
    print("optional_cost:", optional_cost(12345678901234567890123)())
    views("de")
    print("rebound_param:", rebound_param("ab", True)(), rebound_param("ab", False)())
    up = P(1)
    uf = ptr_union(up)
    up.n = 8
    print("ptr_union:", uf())
    loop_optional()
    outer_slot()
    outer_slot_rec()
    us: int32 | str = "x"
    print("union_param:", union_param(us)())
    print("tuple_cost:", tuple_cost(10 ** 30)())
    union_local(True)
    callable_cap()
    aps = [P(1)]
    ag = alias_opt(aps, True)
    aps[0].n = 7
    print("alias_opt:", ag())


main()


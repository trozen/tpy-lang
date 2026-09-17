# A GENERIC generator's yield slot is spelled `::tpy::yield_slot_t<T>` (a
# `val_or_ref<T>`), so the element's value-vs-reference shape is settled at
# instantiation and the generic frame lends exactly where its monomorphic twin
# does. Each lending section mutates the YIELDED value and prints the source
# afterwards -- a read-only section would match CPython even if the yield
# copied. The inverse sections pin the VALUE slot, which a generator gets when
# a yield source does not outlive a suspension; they are read-only because a
# mutation through the copy would NOT reach the source, which is a CPython
# divergence rather than a pinned behaviour.
from tpy import Fn, Own, dispatch, int32, nocopy, readonly
from typing import Iterable, Iterator, Protocol


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Gate:
    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Appendable(Protocol):
    def append(self, v: int32) -> None: ...


class Bin:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    def append(self, v: int32) -> None:
        self.total += v

    @readonly
    def size(self) -> int32:
        return self.total


# free function: the subject -- a loop var over a param container
def each[T](items: Iterable[T]) -> Iterator[T]:  # tpyc: ok
    for it in items:
        yield it


# readonly source + readonly element: the const rides inside the same slot
# (`val_or_ref<const T>`), so the pull still borrows instead of copying
def each_ro[T](items: readonly[Iterable[T]]) -> Iterator[readonly[T]]:  # tpyc: ok
    for it in items:
        yield it


# protocol-bounded `T`: the bound changes nothing about the slot
def rep[T: Appendable](obj: T, times: int32) -> Iterator[T]:  # tpyc: ok
    for _ in range(times):
        yield obj


# a param SUBSCRIPT is a lending source, like the param name itself
def ends[T](items: list[T]) -> Iterator[T]:
    yield items[0]  # tpyc: ok
    yield items[len(items) - 1]


# a frame-resident local's `frame_slot<T>` storage outlives the suspension
def first_then_rest[T](items: list[T]) -> Iterator[T]:
    head = items[0]
    yield head  # tpyc: ok
    for it in items:
        yield it


# context-manager body: the suspension happens inside the `with`, and the
# param container still outlives it
def in_with[T](items: list[T]) -> Iterator[T]:
    with Gate():
        for it in items:
            yield it  # tpyc: ok


# try/finally body: same question one block form over
def in_try[T](items: list[T]) -> Iterator[T]:
    try:
        for it in items:
            yield it  # tpyc: ok
    finally:
        pass


# a `match` arm's yield
def in_match[T](items: list[T], mode: int32) -> Iterator[T]:
    match mode:
        case 0:
            yield items[0]  # tpyc: ok
        case _:
            yield items[1]


# a loop variable over a frame-resident LOCAL container: the local's storage
# outlives the suspension, so the lend is sound -- observed by mutating on the
# first pass over `box` and reading the mutation back on the second. The
# container is filled by `append` rather than written as `[a, b]` because the
# literal form does not compile at a reference instantiation
# (BUGS.md#generic-array-literal-of-references).
def via_local[T](items: list[T]) -> Iterator[T]:
    box: list[T] = []
    for src in items:
        box.append(src)  # tpyc: warning(/may copy T into owned storage/)
    for first in box:
        yield first  # tpyc: ok
    for again in box:
        yield again


# an overload set over an `Fn` param: a lambda argument makes the call take the
# Regime-C path, which analyzes the lambda body in a rolled-back TRIAL
@dispatch
def app[A, B](f: Fn[[A], B], x: A) -> B:
    return f(x)


@dispatch
def app[A, B](f: Fn[[A], B], x: list[A]) -> B:
    return f(x[0])


# a lambda TRIAL ahead of the yielded local: the trial restores a snapshot of
# the per-function state, and a local declared AFTER it must still land in the
# live namespace -- otherwise the frame layout never sees it, so it is neither
# a valid borrow root nor readable after a suspension
def trial_then_local[T](items: list[T], n: int32) -> Iterator[T]:
    r = app(lambda v: v + 1, n)
    head = items[0]
    yield head  # tpyc: ok
    print("trial-local-r", r)
    yield head


# a nested `def` in the body: the slot verdict is parked under the ENCLOSING
# function node, so a nested def analyzed before the yield must leave that node
# (and the frame locals hung off it) reachable, or the frame silently demotes
def with_nested[T](items: list[T]) -> Iterator[T]:
    def bump(a: int32) -> int32:
        return a + 1

    print("nested-helper", bump(1))
    for it in items:
        yield it  # tpyc: ok


# the same question for the non-generic borrow slot: a frame-resident local is a
# valid borrow root, and the rooting check runs after the body -- so the nested
# def must not detach the frame locals it reads
def local_after_nested(x: int32) -> Iterator[Point]:
    def bump(a: int32) -> int32:
        return a + 1

    tmp = Point(bump(x))
    yield tmp  # tpyc: ok
    print("frame-local-src", tmp.x)


# a module global under the same name as the local below, so that local SHADOWS
# it. The slot verdict is settled after the body, where the scope has to be the
# generator's own -- without it the local reads as this durable global and the
# frame lends on the global's lifetime instead of the local's.
shadowed_src: list[Point] = [Point(30), Point(31)]


# the yield root here is the LOCAL `shadowed_src`, frame-resident, so the slot
# lends -- proven by mutating the yielded element and reading the source back
def via_shadowing_local[T](xs: list[T]) -> Iterator[T]:
    shadowed_src = xs[0]
    yield shadowed_src  # tpyc: ok
    yield xs[1]


# INVERSE: an erased `Fn` result carries no ownership, so the slot stays a
# VALUE one and the element is copied out -- read-only here, because mutating
# it would not reach the source (BUGS.md#generic-yield-fn-result-copies)
def mapped[T, U](fn: Fn[[T], U], it: Iterable[T]) -> Iterator[U]:
    for x in it:
        yield fn(x)  # tpyc: ok


# INVERSE: one ephemeral source demotes the WHOLE frame -- the slot is one
# type for every yield, so the `items` loop copies too although its own source
# lends (BUGS.md#generic-yield-mixed-source-demotes-frame). Read-only for the
# same reason as the `Fn` section.
def mixed[T](items: list[T], extra: Iterator[T]) -> Iterator[T]:
    for x in items:
        yield x  # tpyc: ok
    for y in extra:
        yield y


# an `Own[T]`-returning named callee behind the same `Fn` param: the result is
# a fresh value, so the value slot is the only sound choice
def fresh(p: Point) -> Own[Point]:
    return Point(p.x)


# INVERSE: a loop var over an `Iterator[T]` param borrows the inner producer's
# step slot, which the next step overwrites -- the VALUE slot copies it out
def relay[T](it: Iterator[T]) -> Iterator[T]:
    for x in it:
        yield x  # tpyc: ok


# a NAMED callee behind the `Fn` param: sema infers its `U` as `Ref[Point]`,
# so the call site substitutes the slot form and the same generator lends
def ident(p: Point) -> Point:
    return p


class Holder[T]:
    items: list[T]

    def __init__(self, items: list[T]) -> None:
        # the generic owning-slot copy contract, hedged at the declaration
        self.items = items  # tpyc: warning(/may copy list\[T\] into field/)

    # method position on a generic record, over a field container
    def walk(self) -> Iterator[T]:  # tpyc: ok
        for it in self.items:
            yield it


class Cell[T]:
    v: T

    def __init__(self, v: T) -> None:
        # the generic owning-slot copy contract again, at a scalar field: at a
        # reference instantiation this stores a copy, which the warning hedges
        self.v = v  # tpyc: warning(/may copy T into field/)

    @property
    def val(self) -> T:
        return self.v

    # a self FIELD is a lending source
    def twice(self) -> Iterator[T]:
        yield self.v  # tpyc: ok
        yield self.v

    # a self PROPERTY read is a getter CALL, so its provenance is the getter's
    # return -- which borrows the receiver, so the slot still lends
    def prop(self) -> Iterator[T]:
        yield self.val  # tpyc: ok


def main() -> None:
    # reference-type T: the consumer's write reaches the source list
    pts = [Point(1), Point(2)]
    for p in each(pts):
        p.x += 100
    print("ref", pts[0].x, pts[1].x)

    # @nocopy element: a copying slot would not compile at all
    toks = [Tok(1), Tok(2)]
    for t in each(toks):
        t.n += 10
    print("nocopy", toks[0].n, toks[1].n)

    # readonly: a copy out of the source would not compile for a @nocopy
    # element, so reading through the const borrow is the proof here
    for t in each_ro(toks):
        print("ro", t.n)

    # value-type T stays by value (`val_or_ref<int32_t>` holds the value)
    total = 0
    for n in each([1, 2, 3]):
        total += n
    print("value", total)

    # str is a value type too -- the slot owns its std::string
    for w in each(["a", "bb"]):
        print("str", w)

    # protocol-bounded T: the write goes through the bound's method
    b = Bin()
    b.append(1)
    for v in rep(b, 2):
        v.append(10)
    print("bound", b.size())

    # nested generic composition: the inner frame's borrow relays out
    more = [Point(5), Point(6)]
    for p in each(each(more)):
        p.x += 100
    print("nested", more[0].x, more[1].x)

    subs = [Point(1), Point(2)]
    for p in ends(subs):
        p.x += 100
    print("subscript", subs[0].x, subs[1].x)

    locs = [Point(1), Point(2)]
    for p in first_then_rest(locs):
        p.x += 100
    print("local", locs[0].x, locs[1].x)

    # generic generator METHOD over a generic record's field
    h = Holder([Point(7), Point(8)])
    for p in h.walk():
        p.x += 100
    print("method", h.items[0].x, h.items[1].x)

    c = Cell(Point(9))
    for p in c.twice():
        p.x += 100
    print("field", c.v.x)

    for p in c.prop():
        p.x += 100
    print("property", c.v.x)

    # a yield inside a context-manager body
    ws = [Point(1), Point(2)]
    for p in in_with(ws):
        p.x += 100
    print("with", ws[0].x, ws[1].x)

    # a yield inside a try/finally body
    ts = [Point(1), Point(2)]
    for p in in_try(ts):
        p.x += 100
    print("try", ts[0].x, ts[1].x)

    # a yield in a match arm
    mt = [Point(1), Point(2)]
    for p in in_match(mt, 0):
        p.x += 100
    print("match", mt[0].x, mt[1].x)

    # a loop var over a frame-resident local container: the second pass reads
    # back what the first pass wrote through the yielded element
    ls = [Point(1), Point(2)]
    for p in via_local(ls):
        p.x += 100
        print("local-container", p.x)

    # a lambda trial ahead of the yield: the same `head` is yielded twice, so
    # the second pass proves the local survived the suspension in the frame
    tls = [Point(1), Point(2)]
    for p in trial_then_local(tls, 1):
        p.x += 100
    print("trial-local", tls[0].x, tls[1].x)

    # a nested def ahead of the yield: the write still reaches the source list
    nd = [Point(1), Point(2)]
    for p in with_nested(nd):
        p.x += 100
    print("nested-def", nd[0].x, nd[1].x)

    # ... and the non-generic frame-local form: the generator prints its own
    # `tmp` after the resume, so the consumer's write is observed at the source
    for p in local_after_nested(1):
        p.x += 100

    # a local shadowing a module global: the writes reach the argument list,
    # and the global keeps its own values
    sh = [Point(1), Point(2)]
    for p in via_shadowing_local(sh):
        p.x += 100
    print("shadow-local", sh[0].x, sh[1].x)
    print("shadow-global", shadowed_src[0].x, shadowed_src[1].x)

    # a NAMED callee's Ref-stamped return keeps the element a borrow, so the
    # consumer's write reaches the source through the `Fn` param
    ns = [Point(1), Point(2)]
    for p in mapped(ident, ns):
        p.x += 100
    print("named-fn", ns[0].x, ns[1].x)

    # inverse 1: a LAMBDA callee is erased, so nothing says its result
    # borrows and the element is copied out (the named form above is why this
    # section, not that one, pins the value slot)
    ms = [Point(1), Point(2)]
    for p in mapped(lambda q: q, ms):
        print("fn", p.x)

    # ... and an `Own`-returning named callee behind the same param: a fresh
    # value, so copying out is right rather than a divergence
    for p in mapped(fresh, ms):
        print("own-fn", p.x)

    # inverse 2: the Iterator[T] relay hands out a copy of the step element
    rs = [Point(1), Point(2)]
    for p in relay(each(rs)):
        print("relay", p.x)
    print("relay-src", rs[0].x, rs[1].x)

    # inverse 3: one ephemeral source demotes every other yield in the frame
    mi = [Point(1), Point(2)]
    mx = [Point(5)]
    for p in mixed(mi, each(mx)):
        print("mixed", p.x)


main()

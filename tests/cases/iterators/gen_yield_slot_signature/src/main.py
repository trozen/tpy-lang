# A generator's yield slot comes from its SIGNATURE: `Iterator[T]` lends at
# every T, so a generic generator lends exactly where its concrete twin does
# -- a relay of a lending source included -- and `Iterator[Own[T]]` hands
# over. Each lending section writes through the yielded element and prints
# the source (or writes the source and reads the element) afterwards.
from typing import Iterable, Iterator, Protocol
from tpy import Fn, Own, copy, int32, nocopy, readonly


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def val(self) -> int32:
        return self.n


class HasVal(Protocol):
    def val(self) -> int32: ...


@nocopy
class N:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    p: P

    def __init__(self, n: int32) -> None:
        self.p = P(n)


class Gate:
    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def each[T](items: list[T]) -> Iterator[T]:
    for x in items:
        yield x


# closed relay of an Iterator param: its loop target relays the step
def relay_p(it: Iterator[P]) -> Iterator[P]:
    for x in it:
        yield x  # tpyc: ok


# the generic twin lends the same way
def relay[T](it: Iterator[T]) -> Iterator[T]:
    for x in it:
        yield x  # tpyc: ok


# a relay of an inner generic producer the relay owns
def lend_twice[T](items: list[T]) -> Iterator[T]:
    for a in each(items):
        yield a  # tpyc: ok


# a relay beside a param-container yield: both lend
def mixed[T](items: list[T], extra: Iterator[T]) -> Iterator[T]:
    for x in items:
        yield x
    for y in extra:
        yield y  # tpyc: ok


# frame local at an open T: lent from the frame, which reads it after resume
def frame_local[T: HasVal](x: T) -> Iterator[T]:
    y = copy(x)
    yield y  # tpyc: ok
    print("frame-local-after", y.val())


# the same at a value instantiation
def frame_local_any[T](x: T) -> Iterator[T]:
    y = copy(x)
    yield y  # tpyc: ok


# tuple unpack at an open T: the target refers into the source param's
# element, so a write through the yielded element reaches it
def relay_unpack[T](xs: list[tuple[int32, T]]) -> Iterator[T]:
    for i, x in xs:
        yield x  # tpyc: ok


# a callable value's result bound to a frame local first: lent from the frame
def my_map[T, U](fn: Fn[[T], U], it: Iterable[T]) -> Iterator[U]:
    for x in it:
        y = fn(x)
        yield y  # tpyc: ok


def identity(p: P) -> P:
    return p


# a relay over a generic generator expression's loop target
def via_genexpr[T](xs: list[T]) -> Iterator[T]:
    for x in (y for y in xs):
        yield x  # tpyc: ok


# relays inside block forms: with, try/finally, match, while
def in_blocks[T](it: Iterator[T], mode: int32) -> Iterator[T]:
    with Gate():
        for x in it:
            try:
                match mode:
                    case 0:
                        yield x  # tpyc: ok
                    case _:
                        pass
            finally:
                pass


def drain_while[T](it: Iterator[T]) -> Iterator[T]:
    while True:
        for x in it:
            yield x  # tpyc: ok
        break


# a callee defined BELOW, lending its param: the check reads its settled fact
def first_of[T](xs: list[T]) -> Iterator[T]:
    yield first_below(xs)  # tpyc: ok


# a tuple step relayed as it is (its elements stay the source's)
def pair_relay(it: Iterator[tuple[P, int32]]) -> Iterator[tuple[P, int32]]:
    for t in it:
        yield t  # tpyc: ok


def pairs_of(ps: list[P]) -> Iterator[tuple[P, int32]]:
    for p in ps:
        yield (p, p.n)


# a readonly element relayed: the consumer reads the source's live value
def ro_id[T](it: Iterable[T]) -> Iterator[readonly[T]]:
    for x in it:
        yield x  # tpyc: ok


# a readonly element through a callable's frame local: a deduced borrow `U`
# lends as const, a value `U` moves (reading the borrow element's field is
# BUGS.md#readonly-open-elem-borrow-instantiation-field-read)
def ro_map[T, U](fn: Fn[[T], U], it: Iterable[T]) -> Iterator[readonly[U]]:
    for x in it:
        y = fn(x)
        yield y  # tpyc: ok


def double(v: int32) -> int32:
    return v * 2


# a statement unpack at an open T: the target binds as its instantiation's
# twin does (a const value at int32)
def second[T](t: tuple[int32, T]) -> T:
    i, x = t  # tpyc: ok
    return x


# an owning element slot copies a relayed lent element, as the twin's does
def bump_all(src: Iterable[Own[P]]) -> int32:
    s = 0
    for p in src:
        p.n += 1000
        s += p.n
    return s


# a copy of the step breaks the borrow: the frame local is lent instead
def copy_relay[T](it: Iterator[T]) -> Iterator[T]:
    for x in it:
        y = copy(x)
        yield y  # tpyc: ok


# a RETURN of a callable value's open result keeps the default reading:
# it hands back whatever the callable returns
def apply[T, U](fn: Fn[[T], U], x: T) -> U:
    return fn(x)  # tpyc: ok


def first_below[T](xs: list[T]) -> T:
    return xs[0]


# the step relay rule: anything rooted in the current step of an inner
# iterator may be yielded -- a part of it, an alias of a part, the step
# handed back through a lending call, a tuple holding it
def part_relay(it: Iterator[H]) -> Iterator[P]:
    for h in it:
        yield h.p  # tpyc: ok


def part_alias_relay(it: Iterator[H]) -> Iterator[P]:
    for h in it:
        q = h.p
        yield q  # tpyc: ok


def call_relay(it: Iterator[P]) -> Iterator[P]:
    for x in it:
        yield identity(x)  # tpyc: ok


def call_alias_relay(it: Iterator[P]) -> Iterator[P]:
    for x in it:
        q = identity(x)
        yield q  # tpyc: ok


def unpack_alias_relay(it: Iterator[tuple[P, int32]]) -> Iterator[P]:
    for t in it:
        a, k = t
        yield a  # tpyc: ok


def tuple_relay(it: Iterator[P]) -> Iterator[tuple[P, int32]]:
    for x in it:
        yield (x, 1)  # tpyc: ok


# the generic twins: a part of an open-T step's holder, and the step handed
# back through a generic lending call
class Cell[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v


def gpart_relay[T](it: Iterator[Cell[T]]) -> Iterator[T]:
    for c in it:
        yield c.v  # tpyc: ok


def gident[T](x: T) -> T:
    return x


def gcall_relay[T](it: Iterator[T]) -> Iterator[T]:
    for x in it:
        yield gident(x)  # tpyc: ok


# a part of a FRAME-OWNED step (an `Iterator[Own[H]]` source) relayed: it
# lives as long as one step of this generator
def own_src(k: int32) -> Iterator[Own[H]]:
    for i in range(k):
        yield H(i + 50)


def own_part_relay(k: int32) -> Iterator[P]:
    for h in own_src(k):
        yield h.p  # tpyc: ok


# a return copies a scalar element of the step out: it roots nothing
def first_n(it: Iterator[P]) -> tuple[int32, int32]:
    for p in it:
        return (p.n, 1)  # tpyc: ok
    return (0, 0)


# ... a nested literal's copied scalar alike
def first_nested(it: Iterator[P]) -> tuple[tuple[int32, int32], int32]:
    for p in it:
        return ((p.n, 1), 2)  # tpyc: ok
    return ((0, 0), 0)


# a popped value bound to a frame local first is lent from the frame; under
# `Iterator[Own[T]]` the pop is handed over
def pop_local[T](xs: list[T]) -> Iterator[T]:
    while len(xs) > 0:
        y = xs.pop()
        yield y  # tpyc: ok


def pop_own[T](xs: list[T]) -> Iterator[Own[T]]:
    while len(xs) > 0:
        yield xs.pop()  # tpyc: ok


class Box[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    # method position: relays a param iterator
    def relay_m(self, it: Iterator[T]) -> Iterator[T]:
        for x in it:
            yield x  # tpyc: ok


# inverse: a fresh value is handed over only under Iterator[Own[T]], and moves
def clones_own[T](xs: list[T]) -> Iterator[Own[T]]:
    for x in xs:
        yield copy(x)  # tpyc: ok


def make_n(xs: list[N]) -> Iterator[Own[N]]:
    for x in xs:
        yield N(x.n + 1)


def main() -> None:
    a = [P(1), P(2)]
    for p in relay_p(iter(a)):
        p.n += 10
    print("closed-relay", a[0].n, a[1].n)

    b = [P(1), P(2)]
    for p in relay(iter(b)):
        p.n += 10
    print("generic-relay", b[0].n, b[1].n)

    # the consumer writes the source, then reads it back through the element
    c = [P(1), P(2)]
    for p in relay(iter(c)):
        c[0].n = 77
        print("write-source", p.n)
        break

    ns = [N(1), N(2)]
    for n in relay(iter(ns)):
        n.n += 5
    print("nocopy-relay", ns[0].n, ns[1].n)

    d = [P(1), P(2)]
    for p in lend_twice(d):
        p.n += 100
    print("inner-producer", d[0].n, d[1].n)

    e = [P(1)]
    f = [P(5)]
    for p in mixed(e, iter(f)):
        p.n += 100
    print("mixed", e[0].n, f[0].n)

    for q in frame_local(P(3)):
        q.n += 1
    for s in frame_local_any("str"):
        print("frame-local-str", s)

    g = [(1, P(2)), (2, P(102))]
    for p in relay_unpack(g):
        p.n += 10
    print("unpack", g[0][1].n, g[1][1].n)

    h = [P(1), P(2)]
    for p in my_map(identity, h):
        p.n += 10
    print("callable-local", h[0].n, h[1].n)

    k = [P(1), P(2)]
    for p in via_genexpr(k):
        p.n += 10
    print("genexpr", k[0].n, k[1].n)

    # a closed generator expression over the same shape
    for p in (x for x in k):
        p.n += 1
    print("closed-genexpr", k[0].n, k[1].n)

    m = [P(1), P(2)]
    for p in in_blocks(iter(m), 0):
        p.n += 10
    print("blocks", m[0].n, m[1].n)

    w = [P(1), P(2)]
    for p in drain_while(iter(w)):
        p.n += 10
    print("while", w[0].n, w[1].n)

    bx: Box[P] = Box()
    r = [P(1), P(2)]
    for p in bx.relay_m(iter(r)):
        p.n += 10
    print("method", r[0].n, r[1].n)

    # list() collects the relayed lent elements, copying each, as it copies
    # the concrete twin's
    t = [P(1), P(2)]
    got = list(relay(iter(t)))  # tpyc: warning(/copies P elements/)
    print("list", len(got), got[1].n)

    fb = [P(1), P(2)]
    for p in first_of(fb):
        p.n += 10
    print("callee-below", fb[0].n)

    pp = [P(1), P(2)]
    for pt in pair_relay(pairs_of(pp)):
        pt[0].n += 10
    print("tuple-relay", pp[0].n, pp[1].n)

    rp = [P(5), P(6)]
    for ro in ro_id(rp):
        rp[1].n += 100
        print("ro-id", ro.n)
    for q in ro_map(identity, rp):
        rp[0].n += 1
        print("ro-map", 1)
    print("ro-map-src", rp[0].n)
    for v in ro_map(double, [3, 4]):
        print("ro-map-value", v)

    print("unpack-int", second((1, 8)))

    # next() copies the step out, as over any iterator (read-only: the
    # copy would not reach the source)
    nx = [P(1), P(2)]
    rit = relay(iter(nx))
    first = next(rit, P(0))  # tpyc: warning(/copies P into local 'first'/)
    print("next", first.n)

    bo = [P(1), P(2)]
    # (the copy is the warned divergence: the source is not read back here)
    print("own-slot", bump_all(relay(iter(bo))))  # tpyc: warning(/copies P elements/)

    cr = [P(1), P(2)]
    for p in copy_relay(iter(cr)):
        p.n += 100
    print("copy-relay", cr[0].n, cr[1].n)

    print("apply", apply(double, 21))

    hs = [H(1), H(2)]
    for p in part_relay(each(hs)):
        p.n += 10
    print("part", hs[0].p.n, hs[1].p.n)
    for p in part_alias_relay(each(hs)):
        p.n += 10
    print("part-alias", hs[0].p.n, hs[1].p.n)
    cs = [P(1), P(2)]
    for p in call_relay(each(cs)):
        p.n += 10
    print("call", cs[0].n, cs[1].n)
    for p in call_alias_relay(each(cs)):
        p.n += 10
    print("call-alias", cs[0].n, cs[1].n)
    up = [P(1), P(2)]
    for p in unpack_alias_relay(pairs_of(up)):
        p.n += 10
    print("unpack-alias", up[0].n, up[1].n)
    for ts in tuple_relay(each(cs)):
        ts[0].n += 10
    print("tuple-step", cs[0].n, cs[1].n)
    gc = [Cell(P(1)), Cell(P(2))]
    for p in gpart_relay(each(gc)):
        p.n += 10
    print("generic-part", gc[0].v.n, gc[1].v.n)
    for p in gcall_relay(each(cs)):
        p.n += 10
    print("generic-call", cs[0].n, cs[1].n)
    for p in own_part_relay(2):
        later = [P(7), P(8)]
        print("own-part", p.n, len(later))
    fr = first_n(each(cs))
    print("tuple-return", fr[0])
    fnest = first_nested(each(cs))
    print("tuple-return-nested", fnest[0][0])

    pl = [P(1), P(2)]
    for p in pop_local(pl):
        print("pop-local", p.n)
    for ps in pop_local(["a", "b"]):
        print("pop-local-str", ps)
    po = [P(3), P(4)]
    for p in pop_own(po):
        print("pop-own", p.n)

    o = [P(1), P(2)]
    for p in clones_own(o):
        p.n += 100
    print("own", o[0].n, o[1].n)
    for nn in make_n(ns):
        print("own-nocopy", nn.n)


main()

# max / min over ONE iterable and next(it, default) hand back the ELEMENT
# itself where the source is a container: those sections write through the
# result and read the container back. An iterator never lends -- its step is
# valid only until the next one -- so over a generator, a named iterator or
# a class whose __iter__ builds one, the result is a value: a binding copies
# and says so, and the value is the right element.
import asyncio
from typing import Iterable, Iterator
from tpy import ComparableRef, Own, Span, copy, readonly


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v

    def __lt__(self, other: "P") -> bool:
        return self.v < other.v

    def inc(self) -> None:
        self.v += 1


def key_of(p: P) -> int:
    return p.v


def bump(p: P) -> None:
    p.v += 1000


def walk(ps: list[P]) -> Iterator[P]:
    for p in ps:
        yield p


# a generator that REBINDS the object it yields on every step
def fresh(vals: list[int]) -> Iterator[P]:
    for v in vals:
        p = P(v)
        yield p


# a self-iterator overwriting its current element on every step
class Counter:
    cur: P
    n: int

    def __init__(self, n: int) -> None:
        self.cur = P(0)
        self.n = n

    def __iter__(self) -> "Counter":
        return self

    def __next__(self) -> P:
        if self.cur.v >= self.n:
            raise StopIteration
        self.cur = P(self.cur.v + 1)
        return self.cur


def show(tag: str, ps: list[P]) -> None:
    print(tag, [p.v for p in ps])


class Bag:
    items: list[P]

    def __init__(self) -> None:
        self.items = [P(4), P(6), P(5)]

    def __iter__(self) -> Iterator[P]:
        for p in self.items:
            yield p

    def top(self) -> P:
        # method: a returned borrow of a field's element
        return max(self.items, key=key_of)


# free function: a list source, with and without key
def list_fn() -> None:
    ps = [P(3), P(1), P(2)]
    big = max(ps)
    big.v = 30
    small = min(ps, key=key_of)
    small.v = -1
    show("list", ps)
    bump(max(ps, key=key_of))
    show("list arg", ps)
    # an in-place read, then a write through the same in-place result
    print("list read", min(ps).v, max(ps, key=key_of).v)
    bump(min(ps))
    show("list read after", ps)


# the result may be written through, so even a read-only use takes the
# source parameter as a mutable reference; main reads the source back to
# show nothing was copied in
def lowest(ps: list[P]) -> int:
    m = min(ps, key=key_of)
    return m.v


# a mutable return hands the element itself to the caller
def pick_max(ps: list[P]) -> P:
    return max(ps, key=key_of)


# a readonly source gives a readonly element
def ro_source(ps: readonly[list[P]]) -> int:
    m = max(ps, key=lambda p: p.v)  # tpyc: type(readonly[P])
    return m.v


# a Span source
def span_fn(xs: Span[P]) -> None:
    m = min(xs, key=key_of)
    m.v = -5


# an iterator never lends: a binding of the result holds a copy
def iter_fn() -> None:
    ps = [P(3), P(9), P(2)]
    it = walk(ps)
    m = max(it, key=key_of)  # tpyc: warning(/copies P/)
    print("iter named", m.v)
    g = max(walk(ps), key=key_of)  # tpyc: warning(/copies P/)
    print("iter inline", g.v)
    h = min((p for p in ps if p.v > 2), key=key_of)  # tpyc: warning(/copies P/)
    print("iter genexpr", h.v)
    # an inline read copies nothing
    print("iter read", max(walk(ps), key=key_of).v)
    # a named iter() over a list is an iterator too
    it2 = iter(ps)
    q = max(it2, key=key_of)  # tpyc: warning(/copies P into local 'q'/)
    print("iter list", q.v)


# the right element out of a source that rebinds what it yields
def rebind_fn() -> None:
    a = max(fresh([3, 9, 4]), key=key_of)  # tpyc: warning(/copies P/)
    b = max(fresh([3, 9, 4]))  # tpyc: warning(/copies P/)
    c = min(fresh([3, 9, 4]), key=key_of)  # tpyc: warning(/copies P/)
    g = fresh([5, 1, 8])
    d = max(g, key=key_of)  # tpyc: warning(/copies P/)
    print("rebind", a.v, b.v, c.v, d.v)
    e = max(Counter(3), key=key_of)  # tpyc: warning(/copies P/)
    print("self iterator", e.v)
    g2 = fresh([10, 20, 30])
    d0 = P(0)
    x = next(g2, d0)  # tpyc: warning(/copies P/)
    y = next(g2, d0)  # tpyc: warning(/copies P/)
    z = next(g2, d0)  # tpyc: warning(/copies P/)
    print("rebind next", x.v, y.v, z.v)


# a class whose __iter__ builds a fresh iterator cannot lend either
def bag_fn() -> None:
    b = Bag()
    m = max(b, key=key_of)  # tpyc: warning(/copies P/)
    print("bag", m.v)
    t = b.top()
    t.v = 60
    show("bag method", b.items)


# a fresh container: the result is a fresh value, nothing to observe
def fresh_fn() -> None:
    f = max([P(1), P(2)], key=key_of)
    f.v += 1
    print("fresh", f.v)


# rows: the element is itself a container
def rows_fn() -> None:
    rows: list[list[int]] = [[1], [1, 2, 3], [1, 2]]
    longest = max(rows, key=lambda r: len(r))
    longest.append(0)
    print("rows", rows)


# next(it, default): in place, the call acts on the step's element or on the
# default itself; a binding holds a copy, the step being valid only until
# the next one
def next_fn() -> None:
    ps = [P(1), P(2)]
    d = P(0)
    it = iter(ps)
    bump(next(it, d))
    print("next arg", ps[0].v, ps[1].v)
    x = next(it, d)  # tpyc: warning(/copies P/)
    print("next bound", x.v)
    bump(next(it, d))
    print("next default", d.v)
    w = next(it, P(7))  # tpyc: warning(/copies P/)
    print("next fresh", w.v)


# an iterator over a PARAMETER: writing through next's in-place result is a
# mutable use of the list the iterator walks
def next_param(ps: list[P], d: P) -> None:
    it = iter(ps)
    bump(next(it, d))


def show_v(p: P) -> int:
    return p.v


def bump_ret(p: P) -> int:
    p.v += 1000
    return p.v


# over an iterator the call hands back a COPY: a parameter only read takes
# it silently, one the callee writes takes it with the warning -- the write
# lands in the copy (the source is deliberately not read back here)
def copy_arg_fn() -> None:
    ps = [P(3), P(9), P(2)]
    r = show_v(max(walk(ps), key=key_of))  # tpyc: ok
    # an explicit copy says so itself: silent, at an argument and as a
    # receiver the method writes
    c = bump_ret(copy(max(walk(ps), key=key_of)))  # tpyc: ok
    copy(max(walk(ps), key=key_of)).inc()  # tpyc: ok
    print("explicit copy", c, [p.v for p in ps])
    w = bump_ret(max(walk(ps), key=key_of))  # tpyc: warning(/copies P into argument 'p'/)
    print("arg copy", r, w)


# a dict's values view lends the stored values
def dict_values_fn() -> None:
    d = {"a": P(5), "b": P(2)}
    m = max(d.values(), key=key_of)
    m.v = -1
    print("values", d["a"].v)


# an owned return of a copy over an iterator says so
def best_own(ps: list[P]) -> Own[P]:
    return max(walk(ps), key=key_of)  # tpyc: warning(/copies P into owned storage/)


class Stepper:
    ps: list[P]

    def __init__(self) -> None:
        self.ps = [P(1), P(2)]

    def bump_first(self, d: P) -> None:
        # method: next over an iterator of a field, written in place
        it = iter(self.ps)
        bump(next(it, d))


def next_positions() -> None:
    s = Stepper()
    d0 = P(0)
    s.bump_first(d0)
    print("next method", [p.v for p in s.ps])
    ps = [P(4)]
    d = P(0)

    def inner() -> None:
        # closure: the captured list and default
        it = iter(ps)
        bump(next(it, d))
        bump(next(it, d))

    inner()
    print("next closure", ps[0].v, d.v)
    print("own", best_own([P(3), P(8)]).v)


class Pq:
    v: int
    q: P

    def __init__(self, v: int) -> None:
        self.v = v
        self.q = P(v * 10)


def walk_pq(xs: list[Pq]) -> Iterator[Pq]:
    for x in xs:
        yield x


def pq_key(x: Pq) -> int:
    return x.v


# a field read off a fresh result is held as a copy too, and says so
def projected_fn() -> None:
    xs = [Pq(3), Pq(9)]
    m = max(walk_pq(xs), key=pq_key).q  # tpyc: warning(/copies P into local 'm'/)
    it = iter(xs)
    d = Pq(0)
    k = next(it, d).q  # tpyc: warning(/copies P into local 'k'/)
    print("projected", m.v, k.v)


# a generic body over a protocol-typed source decides before instantiation:
# holding the result is the hedged copy contract (declared, so the case only
# reads it); a `list[T]` parameter is the container spelling (TODO.md,
# "Per-instantiation result form for a generic body")
def top_any[T: ComparableRef](xs: Iterable[T]) -> Own[T]:
    m = max(xs)  # tpyc: warning(/may copy T into local 'm' if not a value type/)
    return m


# ... and over a `list[T]` source too: a borrow of an open `T` has no slot
# yet, so the generic body holds the same hedged copy
def top_list[T: ComparableRef](xs: list[T]) -> Own[T]:
    m = max(xs)  # tpyc: warning(/may copy T into local 'm' if not a value type/)
    return m


# generator body: the borrow lives across a suspension
def gen_body(ps: list[P]) -> Iterator[int]:
    m = min(ps, key=key_of)
    yield m.v
    m.v = 77
    yield m.v


# async body
async def async_body(ps: list[P]) -> int:
    m = max(ps, key=key_of)
    await asyncio.sleep(0)
    m.v = 88
    return m.v


# an empty source raises, whatever the element type
def empty_fn() -> None:
    none: list[P] = []
    try:
        print(max(none, key=key_of).v)
    except ValueError:
        print("empty ValueError")


# value types are untouched
def values_fn() -> None:
    fives = [5]
    i5 = iter(fives)
    print("values", max([3, 1, 2]), min(["b", "a"]), next(i5, 0), next(i5, 0))


def main() -> None:
    list_fn()
    ps = [P(5), P(4), P(6)]
    print("lowest", lowest(ps))
    show("lowest after", ps)
    r = pick_max(ps)
    r.v = 66
    show("return", ps)
    print("ro", ro_source(ps))
    span_fn(ps)
    show("span", ps)
    iter_fn()
    rebind_fn()
    bag_fn()
    fresh_fn()
    rows_fn()
    next_fn()
    nps = [P(1), P(2)]
    next_param(nps, P(0))
    show("next param", nps)
    copy_arg_fn()
    dict_values_fn()
    projected_fn()
    print("generic", top_any([P(3), P(8)]).v, top_list([P(4), P(6)]).v)
    next_positions()
    for x in gen_body(ps):
        print("gen", x)
    show("gen after", ps)
    print("async", asyncio.run(async_body(ps)))
    show("async after", ps)
    empty_fn()
    values_fn()


main()

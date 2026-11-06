# Callables at generic slots: the instantiation's convention, a lambda at an
# open return, map, a generic's Callable result and the refusals' spellings.
from typing import Callable
from tpy import Fn, Own, int32, copy
from fixtures import P, ident, mk


# generic slots keep reading the instantiation's convention.
def fill[T, U](f: Fn[[T], U], x: T, out: list[U]) -> None:
    out.append(f(x))


# U bound from the list before the callable: the same verdict either order.
def fill2[T, U](out: list[U], f: Fn[[T], U], x: T) -> None:
    out.append(f(x))


def build[T](f: Fn[[T], Own[T]], x: T) -> Own[T]:
    return f(x)


def consume[U](f: Fn[[P], U], p: P) -> U:
    return f(p)


def forward(f: Fn[[P], P], p: P) -> int32:
    # An Fn value at a template slot whose return is open lends as a def.
    r = consume(f, p)  # tpyc: ok
    r.n += 10
    return r.n


def sec_generic() -> None:
    p = P(1)
    out: list[P] = []
    fill(mk, p, out)  # tpyc: ok
    fill(ident, P(1), out)
    g: Callable[[P], P] = ident
    # A lending Callable infers U as the def does.
    fill(g, P(1), out)  # tpyc: ok
    fill2(out, mk, p)  # tpyc: ok
    fill2(out, g, P(1))  # tpyc: ok
    out[0].n += 10
    # The lending legs copy where CPython aliases
    # (BUGS.md#generic-spelled-type-args-copy-where-inferred-lend); their
    # sources are temporaries, so the output cannot show it: the C++
    # snapshot pins the copy.
    out[1].n += 10
    out[2].n += 20
    print("generic:", p.n, out[0].n, out[1].n, out[2].n, out[3].n, len(out))
    # An owning callable at a generic `Fn[[T], Own[T]]` slot: the result is
    # a new object, moved into `m`.
    m = build(mk, p)  # tpyc: ok
    m.n += 100
    print("generic own:", p.n, m.n, forward(ident, p), p.n)


# a lambda at a Callable slot whose return is a type parameter returns its
# body's value: the copy is warned, so this leg only reads it.
def pick_cb[T, U](f: Callable[[T], U], x: T) -> Callable[[T], U]:
    return f


def sec_generic_lambda() -> None:
    p = P(1)
    h = pick_cb(lambda q: q, p)  # tpyc: warning(/copies P into owned storage/)
    print("generic lambda:", h(p).n)


# a lending Callable at map's open return aliases, as map(ident, ...) does;
# an Fn handing back a value fits a Callable doing the same.
def own_fn_to_callable(f: Fn[[P], Own[P]], x: P) -> int32:
    c: Callable[[P], Own[P]] = f  # tpyc: ok
    return c(x).n


def sec_map() -> None:
    qs = [P(0), P(0)]
    f: Callable[[P], P] = lambda q: q
    for q in map(f, qs):  # tpyc: ok
        q.n += 1
    print("map:", qs[0].n, qs[1].n, own_fn_to_callable(mk, qs[0]))


# a generic's Callable result is spelled as the std::function it is: at a
# value type the declared Callable itself, at a class its Own form.
def pick0[T](f: Callable[[], T]) -> Callable[[], T]:
    return f


def one() -> int32:
    return 1


def mk_pair() -> Own[tuple[P, int32]]:
    return (P(4), 2)


def mk_opt() -> Own[P | None]:
    return P(7)


def sec_identity() -> None:
    a: Callable[[], int32] = one
    fs = [a, pick0(a)]  # tpyc: ok
    cm: Callable[[P], Own[P]] = mk
    seed = P(0)
    ms = {"m": cm, "g": pick_cb(mk, seed)}  # tpyc: ok
    # A tuple holding a class and an Optional of one are held by value too.
    t: Callable[[], Own[tuple[P, int32]]] = mk_pair
    ts = [t, pick0(t)]  # tpyc: ok
    o: Callable[[], Own[P | None]] = mk_opt
    opts = [o, pick0(o)]  # tpyc: ok
    ov = opts[1]()
    print("identity:", fs[0](), fs[1](), ms["g"](P(1)).n, ts[1]()[0].n,
          ov.n if ov is not None else 0)


# the spellings the generic-slot refusals name compile: a copying lambda at
# Callable[[P], Own[P]], an owning Callable for the generic's result, and an
# owning Fn at Callable[[], Own[T]].
def lend_owned[T](f: Fn[[], Own[T]], like: T) -> Callable[[], Own[T]]:
    g: Callable[[], Own[T]] = f  # tpyc: ok
    return g


def mk0() -> Own[P]:
    return P(3)


def sec_hatches() -> None:
    p = P(1)
    c: Callable[[P], Own[P]] = lambda q: copy(ident(q))  # tpyc: ok
    h = pick_cb(c, p)
    r = h(p)
    r.n += 5
    m: Callable[[], Own[P]] = mk0
    k: Callable[[], Own[P]] = pick0(m)  # tpyc: ok
    g = lend_owned(mk0, p)
    print("hatches:", p.n, r.n, k().n, g().n)


def main() -> None:
    sec_generic()
    sec_generic_lambda()
    sec_map()
    sec_identity()
    sec_hatches()

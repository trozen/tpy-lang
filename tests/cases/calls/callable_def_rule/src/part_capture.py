# The capture-root check of a lambda or nested def at a Callable slot:
# definition order, sibling defs, and nested defs capturing no state.
from typing import Callable
from tpy import Own, int32
from fixtures import P, Pair, ident, via


# the capture-root check reads each call's signature, so a helper defined
# after the closure lends its argument, never a capture.
def sec_order() -> None:
    p = P(1)
    k: Callable[[P], P] = lambda q: later_ident(q)  # tpyc: ok

    def get(q: P) -> P:
        return later_ident(q)
    j: Callable[[P], P] = get  # tpyc: ok
    k(p).n += 1
    j(p).n += 10
    print("order:", p.n)


# sibling defs that lend their own arguments: a capture-free one stands at a
# Callable slot by its signature, and so does one calling it, since what it
# captures is a def capturing no state; one calling a def that captures
# state (`side` captures h) takes an Fn slot (a Callable one refuses it,
# error_callable_nested_def_sibling_state).
def sec_siblings(h: P) -> None:
    def get(q: P) -> P:
        return later_ident(q)

    def middle(q: P) -> P:
        return get(q)

    def side() -> int32:
        return ident(h).n

    def guarded(q: P) -> P:
        return q if side() > 0 else q
    g: Callable[[P], P] = get  # tpyc: ok
    # middle captures get, a def with no state: its signature decides.
    m: Callable[[P], P] = middle  # tpyc: ok
    lm: Callable[[P], P] = lambda q: middle(q)  # tpyc: ok
    s: Callable[[P], P] = lambda q: ident(q) if side() > 0 else q  # tpyc: ok
    p = P(1)
    via(lambda q: middle(q), p)
    via(middle, p)
    # guarded captures side, which captures h: an Fn slot takes it.
    via(guarded, p)
    g(p).n += 10
    s(p).n += 100
    m(p).n += 1000
    lm(p).n += 10000
    print("siblings:", h.n, p.n)


def tup_of(q: P) -> Own[tuple[P, int32]]:
    return (P(q.n), 1)


# a nested def capturing no state is judged by its signature, as a module
# function is: a result through a local or a loop, or with an int32 or a
# str captured, aliases an argument, as in CPython.
def sec_nested_locals() -> None:
    def bigger(a: P, b: P) -> P:
        best = a
        if b.n > a.n:
            best = b
        return best

    def get(q: P) -> P:
        r = q
        return r

    def loopy(xs: list[P]) -> P:
        best = xs[0]
        for x in xs:
            if x.n > best.n:
                best = x
        return best
    limit = 3

    def capped(a: P, b: P) -> P:
        return a if a.n > limit else b
    tag = "big"

    def tagged(a: P, b: P) -> P:
        return a if len(tag) > a.n else b
    lend: Callable[[P], P] = lambda q: q

    def through(q: P) -> P:
        return lend(q)
    make: Callable[[int32], Own[P]] = lambda i: P(i)

    def guarded(q: P) -> P:
        return q if make(1).n > 0 else q
    maybe_lend: Callable[[P], P] | None = ident

    def maybe(q: P) -> P:
        f = maybe_lend
        if f is not None:
            return f(q)
        return q
    k: Callable[[P, P], P] = lambda x, y: bigger(x, y)  # tpyc: ok
    g: Callable[[P], P] = get  # tpyc: ok
    m: Callable[[list[P]], P] = loopy  # tpyc: ok
    c: Callable[[P, P], P] = capped  # tpyc: ok
    # a str capture holds no reference type: not state either.
    t: Callable[[P, P], P] = tagged  # tpyc: ok
    # a captured Callable is no state: its result points into its arguments.
    w: Callable[[P], P] = through  # tpyc: ok
    v: Callable[[P], P] = lambda q: guarded(q)  # tpyc: ok
    # so is one inside an Optional: the rule follows the Callable's return.
    om: Callable[[P], P] = maybe  # tpyc: ok
    p = P(1)
    o = P(5)
    k(p, o).n += 10
    g(p).n += 100
    xs = [P(3), P(9), P(4)]
    m(xs).n += 1000
    c(o, p).n += 10000
    t(p, o).n += 100000
    print("nested locals:", p.n, o.n, xs[1].n)
    s = P(2)
    w(s).n += 1
    u = P(7)
    v(u).n += 2
    r = P(4)
    om(r).n += 3
    print("nested locals captured callable:", s.n, u.n, r.n)
    pair = Pair(1, 2)

    def paired(a: P, b: P) -> P:
        return a if pair.a < pair.b else b
    ns = [1, 2]

    def counted(a: P, b: P) -> P:
        return a if ns[0] < ns[1] else b
    mk_cb: Callable[[], Callable[[P], P]] = lambda: ident

    def chained(q: P) -> P:
        return mk_cb()(q)
    # a pointer-free value record, a pending list of ints and a Callable
    # returning a rule-R Callable hold nothing a borrow could point into.
    pr: Callable[[P, P], P] = paired  # tpyc: ok
    cn: Callable[[P, P], P] = counted  # tpyc: ok
    ch: Callable[[P], P] = chained  # tpyc: ok
    a = P(1)
    b = P(2)
    pr(a, b).n += 10
    cn(b, a).n += 100
    ch(a).n += 1000
    print("nested locals values:", a.n, b.n)
    tup_cb: Callable[[P], tuple[P, int32]] = tup_of

    def tupled(q: P) -> P:
        tp = tup_cb(q)
        return q if tp[1] > 0 else q
    # a Callable returning a tuple by value hands the caller its own copy,
    # so capturing it is no state.
    tu: Callable[[P], P] = tupled  # tpyc: ok
    e = P(1)
    tu(e).n += 10
    print("nested locals by-value callable:", e.n)


def main() -> None:
    sec_order()
    sec_siblings(P(41))
    sec_nested_locals()


def later_ident(p: P) -> P:
    return p

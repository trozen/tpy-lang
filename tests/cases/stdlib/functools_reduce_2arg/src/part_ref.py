# functools.reduce over a reference type: a combinator returning one of its
# arguments takes the lending form (the result IS the list's element, as in
# CPython), a fresh one the owning form; str folds, the 3-argument lending
# form over a list, and the empty input's TypeError.
from functools import reduce
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bigger(a: P, b: P) -> P:
    return a if a.n > b.n else b


def add_p(a: P, b: P) -> Own[P]:
    # writes its first argument: the first step's is the list's element
    a.n += 1000
    return P(a.n + b.n)


def concat(a: str, b: str) -> str:
    return a + b


def lend2() -> None:
    ps = [P(1), P(5), P(3)]
    r = reduce(bigger, ps)  # tpyc: ok
    # the result is the element itself
    r.n += 100
    print("ref-lend2", r.n, ps[1].n)


def own2() -> None:
    ps = [P(1), P(5), P(3)]
    q = reduce(add_p, ps)  # tpyc: ok
    q.n += 1
    print("ref-own2", q.n, ps[0].n)


def fresh_lambda() -> None:
    ps = [P(1), P(5), P(3)]
    s = reduce(lambda a, b: P(a.n + b.n), ps)  # tpyc: ok
    s.n += 1
    print("ref-lambda", s.n, ps[0].n)


def pick_lambda() -> None:
    ps = [P(1), P(5), P(3)]
    # a lambda returning one of its parameters lends: the lending form
    t = reduce(lambda a, b: a if a.n > b.n else b, ps)  # tpyc: ok
    t.n += 7
    print("ref-pick", t.n, ps[1].n)


def last_lambda() -> None:
    ps = [P(1), P(5), P(3)]
    u = reduce(lambda a, b: b, ps)  # tpyc: ok
    u.n += 1
    print("ref-last", u.n, ps[2].n)


def lend3() -> None:
    ps = [P(1), P(5), P(3)]
    p0 = P(9)
    # a lending combinator with an initial value takes a list
    r = reduce(bigger, ps, p0)  # tpyc: ok
    r.n += 1
    print("ref-lend3", r.n, p0.n, ps[1].n)


def empty() -> None:
    none: list[P] = []
    try:
        r = reduce(bigger, none)
        print("ref-empty", r.n)
    except TypeError:
        # The message text differs between CPython versions.
        print("ref-empty", "TypeError")


def main() -> None:
    lend2()
    own2()
    fresh_lambda()
    pick_lambda()
    last_lambda()
    lend3()
    print("ref-str", reduce(concat, ["a", "b", "c"]))  # tpyc: ok
    empty()

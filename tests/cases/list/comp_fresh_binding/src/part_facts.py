# Facts of the hidden name do not hold for the variable, and still hold for
# the enclosing name after the comprehension, less what the body kills.
from typing import Callable, Optional
from tpy import Array, int32


class N:
    v: Optional[str]

    def __init__(self, v: Optional[str]) -> None:
        self.v = v

    def ready(self) -> bool:
        return True

    def clear_v(self) -> bool:
        self.v = None
        return True


def ranged(xs: list[int32], idx: list[int32]) -> None:
    i = 0
    if i < len(xs):
        # The inner i is unproven: checked; the outer i keeps its range.
        print("range", [xs[i] for i in idx])  # tpyc: bounds_checked(xs)
        print("range_after", xs[i])  # tpyc: bounds_safe(xs)


def len_bounded(xs: list[int32], rows: list[list[int32]]) -> None:
    for i in range(len(xs)):
        # `i`'s range is bounded by len(xs); hiding xs keeps its concrete part.
        print("hi_len", [len(xs) for xs in rows], xs[i])  # tpyc: bounds_safe(xs)
        if i > 0:
            # ... and `i`'s non-zero lower bound. (`range(1, len(xs))` would
            # be the plainer head, but its loop variable keeps its zero
            # check even without a comprehension, so the guard pins it.)
            print("hi_len_div", [len(xs) for xs in rows], 10 // i)  # tpyc: div_safe(i)


def none_elem(v: Optional[int], opts: list[Optional[int]]) -> None:
    if v is not None:
        try:
            r = [v + 1 for v in opts]  # tpyc: warning(/Potential None access/)
            print("none", r)
        except TypeError:
            print("none caught")
        print("none_after", v + 1)  # tpyc: ok


def div(d: int32, ds: list[int32]) -> None:
    if d > 0:
        try:
            r = [10 // d for d in ds]  # tpyc: div_checked(d)
            print("div", r)
        except ZeroDivisionError:
            print("div caught")
        print("div_after", 10 // d)  # tpyc: div_safe(d)


def field_fact(ns: list[N]) -> None:
    n = N("hello")
    if n.v is not None:
        # A call on the inner n does not kill the outer n.v fact.
        ys = [n.ready() for n in ns]  # tpyc: ok
        print("field_fact", ys, n.v.upper())  # tpyc: ok
        n.v = "bye"
        print("field_fact_after", n.v, ns[0].v)


def use(f: Callable[[N], bool], p: N) -> bool:
    return f(p)


def lambda_in_comp(rows: list[list[N]]) -> None:
    n = N("hello")
    if n.v is not None:
        # A lambda parameter in the comprehension kills no outer n.v fact.
        r = [use(lambda n: n.ready(), row[0]) for row in rows]  # tpyc: ok
        print("lambda_in_comp", r, n.v.upper())  # tpyc: ok
        n.v = None
        print("lambda_in_comp_after", n.v)


def lam_param_mut(x: list[int32], rows: list[list[int32]]) -> int32:
    # The lambda's x is its parameter: the outer x stays unwritten (const).
    f: Callable[[list[int32]], int32] = lambda x: x.pop()  # tpyc: ok
    return f(rows[0]) + len(x)


def nested_kill(rows: list[list[N]], ks: list[list[int32]]) -> None:
    n = N("hello")
    if n.v is not None:
        # A nested comprehension's own n does not kill the outer n.v fact...
        r = [[n.ready() for n in row] for row in rows]  # tpyc: ok
        print("nested_kill", r, n.v.upper())  # tpyc: ok
        # ... a nested call on the outer n does.
        q = [[n.clear_v() for k in row] for row in ks]
        w = n.v  # tpyc: type(/None/)
        print("nested_kill_outer", q, w)


def tick() -> bool:
    return True


def closure_path(ns: list[N]) -> None:
    n = N("a")

    def clear() -> None:
        n.v = None

    # The closure writes the outer n.v; a call inside does not kill the
    # inner n's fact.
    print("closure_path", [n.v.upper() for n in ns if n.v is not None if tick()])  # tpyc: ok
    clear()
    print("closure_path_after", n.v)


def set_dict_facts(v: Optional[int32], xs: list[int32],
                   idx: list[int32]) -> None:
    i = 0
    if i < len(xs) and v is not None:
        # The inner i and v are the elements: unproven, never None.
        s = {xs[i] + v for i in idx for v in idx}  # tpyc: bounds_checked(xs)
        d = {i: xs[i] for i in idx}  # tpyc: bounds_checked(xs)
        print("set_dict_facts", sorted(s), d, xs[i] + v)  # tpyc: bounds_safe(xs)


def array_facts(xs: list[int32], idx: Array[int32, 2],
                pairs: Array[tuple[int32, int32], 2]) -> None:
    i = 0
    if i < len(xs):
        # The range arm, the source arm and the unpack arm (a global hidden).
        a = [xs[i] for i in range(2)]  # tpyc: bounds_checked(xs)
        b = [xs[i] for i in idx]  # tpyc: bounds_checked(xs)
        c = [G + i for G, i in pairs]  # tpyc: ok
        print("array_facts", a[1], b[0], c[1], G, xs[i])  # tpyc: bounds_safe(xs)


def real_kill(xs: list[int]) -> None:
    v: Optional[int] = 1

    def poke() -> int:
        nonlocal v
        v = None
        return 0

    if v is not None:
        # poke() writes the outer v: its fact dies after the comprehension.
        print("kill", [poke() + v for v in xs])  # tpyc: ok
        try:
            y = v + 1  # tpyc: warning(/Potential None access/)
            print("kill", y)
        except TypeError:
            print("kill TypeError")


def walrus(xs: list[int32]) -> None:
    # A filter walrus binds the enclosing scope.
    last = 0
    r = [x for x in xs if (last := x) > 1]  # tpyc: ok
    print("walrus", r, last)


def lambda_param(xs: list[int32]) -> None:
    i = 0
    if i < len(xs):
        f: Callable[[int32], int32] = lambda i: i * 2  # tpyc: ok
        # The lambda's i is unproven; the outer i keeps its range after.
        g: Callable[[int32], int32] = lambda i: xs[i]  # tpyc: bounds_checked(xs) warning(/copies the object 'xs'/)
        print("lambda_param", f(7), g(0), xs[i])  # tpyc: bounds_safe(xs)


def two_clauses(ps: list[N], qs: list[list[N]]) -> None:
    x = N("x")
    if x.v is not None:
        # Clause 1 reads clause 0's x, not the outer one.
        print("two", len([k.ready() for x in qs for k in x]), x.v.upper())  # tpyc: ok
        x.v = None
        print("two_after", x.v, qs[0][0].v)


G: int32 = 7


def global_hidden(xs: list[int32]) -> None:
    global G
    G = 8
    # A `global` name hidden by the variable; the global after is the outer.
    print("global", [G * 2 for G in xs], G)  # tpyc: ok


def nonlocal_hidden(xs: list[int32]) -> None:
    n = 1

    def inner() -> int32:
        nonlocal n
        n += 10
        return sum([n + 1 for n in xs])  # tpyc: ok
    print("nonlocal", inner(), n)


def main() -> None:
    global_hidden([1, 2])
    nonlocal_hidden([1, 2])
    ranged([10, 20, 30], [2, 1])
    len_bounded([1, 2], [[5]])
    none_elem(1, [1, None, 3])
    none_elem(1, [1, 3])
    div(2, [5, 0])
    div(2, [5])
    field_fact([N("inner")])
    nested_kill([[N("a")]], [[1]])
    closure_path([N("x"), N(None)])
    set_dict_facts(1, [10, 20, 30], [2, 1])
    arr_idx: Array[int32, 2] = [1, 0]
    arr_pairs: Array[tuple[int32, int32], 2] = [(1, 2), (3, 4)]
    array_facts([10, 20, 30], arr_idx, arr_pairs)
    real_kill([10, 20])
    walrus([1, 2, 3])
    lambda_param([4])
    two_clauses([N("a")], [[N("b")]])
    lambda_in_comp([[N("a")]])
    rows = [[1, 2]]
    print("lam_param_mut", lam_param_mut([5], rows), rows)
